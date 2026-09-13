"""End-to-end governed calendar import (ADR-0004): deploy a calendar_import instance bound to a
source + destination connection, then start the BACKGROUND import job and poll it — every eligible
appointment is created, re-running skips them all (idempotent, append-only), a silent destination
write failure is reported as failed, and only confirmed+future appointments are eligible. Driven
through mock connectors (no network); the background worker runs inline (in a joined thread) so the
job is complete by the time the POST returns.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.api.v1 import instances as instances_api
from app.config import settings
from app.connectors import base as connectors_base
from app.connectors.base import Appointment, PatientRef
from app.db.models import (
    AgentInstance,
    Connection,
    EpisodeEvent,
    ImportJob,
    Run,
    Task,
    TaskEvent,
    Template,
    Tenant,
)
from app.main import app
from app.sync import jobs

_NAME = "calendar_import_test"
_ORG = "org_calendar_import"
_KEY = Fernet.generate_key().decode()

# Dates are relative to now so the confirmed+future safety filter (ADR-0004) is exercised without
# rotting: the window is wide enough to hold a past appointment (so it reaches the filter, not the
# window cut), and the eligible ones sit a week out.
_NOW = datetime.now(timezone.utc)
_WINDOW_FROM = (_NOW - timedelta(days=30)).strftime("%Y-%m-%d")
_WINDOW_TO = (_NOW + timedelta(days=30)).strftime("%Y-%m-%d")
_FUTURE = (_NOW + timedelta(days=7)).strftime("%Y-%m-%dT09:00:00+00:00")
_FUTURE2 = (_NOW + timedelta(days=8)).strftime("%Y-%m-%dT10:00:00+00:00")
_PAST = (_NOW - timedelta(days=7)).strftime("%Y-%m-%dT09:00:00+00:00")


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                return await fn(s)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _db_reachable() -> bool:
    try:
        _run_db(lambda s: s.execute(select(1)))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Supabase DB not reachable")

client = TestClient(app)


# --- mock connectors sharing a dest store across the per-appointment runs -----------------

class _FakeSource:
    def __init__(self, appts: dict[str, Appointment]):
        self._appts = appts

    def list_appointments(self, window):
        return list(self._appts.values())

    def get_appointment(self, ref):
        return self._appts.get(ref)

    def verify(self):  # connect-time credential check — the fake always authenticates
        pass

    def close(self):
        pass


class _FakeDest:
    """The destination's two entities, both create-only (ADR-0005 D1): patient cards and
    appointments. `create_patient` ALWAYS appends — like the real thing, it has no idea whether
    this person already has a card — so `cards` is an honest duplicate count. Matching mirrors the
    real rule: name + a real date of birth binds; with no usable date the strict lookup never
    binds, so a new card is created rather than risking someone else's (D3/D4)."""

    def __init__(self, store, write_fails, cards=None, bound=None):
        self._store, self._write_fails = store, write_fails
        self._cards = cards if cards is not None else []
        self._bound = bound if bound is not None else {}

    def find_appointment(self, ref):
        return self._store.get(ref)

    def find_patient(self, patient, *, strict: bool = True):
        if strict and not patient.geburtsdatum:
            return None
        for card in self._cards:
            if (card["vorname"], card["nachname"], card["geburtsdatum"]) == (
                patient.vorname, patient.nachname, patient.geburtsdatum
            ):
                return PatientRef(id=card["id"], vorname=card["vorname"], nachname=card["nachname"])
        return None

    def create_patient(self, patient):
        card = {
            "id": 1000 + len(self._cards), "vorname": patient.vorname,
            "nachname": patient.nachname, "geburtsdatum": patient.geburtsdatum,
            "strasse": patient.strasse, "plz": patient.plz, "ort": patient.ort,
            "email": patient.email,
        }
        self._cards.append(card)
        return PatientRef(id=card["id"], vorname=card["vorname"], nachname=card["nachname"])

    def create_appointment(self, appt, *, patient_id: int, force: bool = False):
        if not self._write_fails:  # silent failure = accept but never persist
            self._store[appt.ref] = appt
            self._bound[appt.ref] = patient_id

    def verify(self):  # connect-time credential check — the fake always authenticates
        pass

    def close(self):
        pass


def _install_connectors(monkeypatch, appts, dest_store, write_fails=False, cards=None, bound=None):
    def build(adapter, base_url, creds, **opts):
        if adapter == "healthyfeet":
            return _FakeSource(appts)
        if adapter == "thevea":
            return _FakeDest(dest_store, write_fails, cards, bound)
        raise ValueError(adapter)

    monkeypatch.setattr(connectors_base, "build_connector", build)


def _contract() -> dict[str, Any]:
    return {
        "template": _NAME,
        "risk": "medium",
        "agent_class": "workflow",
        "parameters": {"window_from": {"type": "text"}, "window_to": {"type": "text"}},
        "required_connections": ["source", "destination"],
        "state": {
            "source_appt": {"provider": "source", "query": "appointment"},
            "dest_patient": {"provider": "destination_patient", "query": "patient_for_appointment"},
            "dest_match": {"provider": "destination", "query": "appointment_by_ref"},
        },
        "steps": [
            {
                # ADR-0005 D2: find-or-create is the tool's business; the postcondition rules on
                # whether a card actually exists by re-querying the destination.
                "id": "ensure_patient",
                "kind": "enforced",
                "tools": ["create_patient"],
                "preconditions": [
                    {"check": "source_appt.exists == true"},
                    {"check": "dest_match.exists == false"},
                ],
                "execute": {"adapter": "registry", "tool": "create_patient"},
                "postconditions": [{"check": "dest_patient.exists == true"}],
            },
            {
                "id": "copy_appointment",
                "kind": "enforced",
                "tools": ["create_appointment"],
                "preconditions": [
                    {"check": "source_appt.exists == true"},
                    {"check": "dest_match.exists == false"},
                ],
                "execute": {"adapter": "registry", "tool": "create_appointment"},
                "postconditions": [{"check": "dest_match.exists == true"}],
            },
        ],
    }


def _cleanup() -> None:
    async def go(s: AsyncSession):
        run_ids = list(
            (await s.execute(select(Run.id).where(Run.template_name == _NAME))).scalars()
        )
        if run_ids:
            await s.execute(delete(EpisodeEvent).where(EpisodeEvent.run_id.in_(run_ids)))
            await s.execute(delete(Run).where(Run.id.in_(run_ids)))
        iids = list(
            (
                await s.execute(
                    select(AgentInstance.id)
                    .join(Template, Template.id == AgentInstance.template_id)
                    .where(Template.name == _NAME)
                )
            ).scalars()
        )
        if iids:
            task_ids = list(
                (await s.execute(select(Task.id).where(Task.instance_id.in_(iids)))).scalars()
            )
            if task_ids:
                await s.execute(delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids)))
            await s.execute(delete(ImportJob).where(ImportJob.instance_id.in_(iids)))
            if task_ids:
                await s.execute(delete(Task).where(Task.id.in_(task_ids)))
        for iid in iids:
            inst = await s.get(AgentInstance, iid)
            if inst is not None:
                await s.delete(inst)
        await s.execute(delete(Template).where(Template.name == _NAME))
        t = (
            await s.execute(select(Tenant).where(Tenant.clerk_org_id == _ORG))
        ).scalar_one_or_none()
        if t is not None:
            await s.execute(delete(Connection).where(Connection.tenant_id == t.id))
            await s.delete(t)
        await s.commit()

    _run_db(go)


def _inline_spawn(job_id, instance_id, tenant_id, window):
    """Run the import worker synchronously (own thread + event loop, joined) so the job is complete
    by the time POST returns — deterministic for tests, no polling loop."""
    t = threading.Thread(
        target=lambda: asyncio.run(jobs.execute_import_job(job_id, instance_id, tenant_id, window))
    )
    t.start()
    t.join()


def _import(instance_id: str) -> dict:
    """Start the import and return the finished job (spawn runs inline, so it's already done)."""
    started = client.post(f"/api/v1/instances/{instance_id}/import")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    return client.get(f"/api/v1/instances/{instance_id}/import/{job_id}").json()


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", _KEY)
    monkeypatch.setattr(settings, "task_shadow_enabled", False)
    monkeypatch.setattr(instances_api, "spawn_import_job", _inline_spawn)

    async def _tenant() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                from app.db import repo

                t = await repo.tenant_for_org(s, _ORG, name=_ORG)
                await s.commit()
                return t
        finally:
            await engine.dispose()

    app.dependency_overrides[current_tenant] = _tenant
    _cleanup()
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _deploy() -> str:
    assert client.post("/api/v1/templates", json={"contract": _contract()}).status_code == 200
    assert client.post(f"/api/v1/templates/{_NAME}/publish").status_code == 200
    src = client.post(
        "/api/v1/connections",
        json={"adapter": "healthyfeet", "type": "calendar", "credentials": {"username": "u", "password": "p"}},
    ).json()["id"]
    dst = client.post(
        "/api/v1/connections",
        json={"adapter": "thevea", "type": "calendar", "credentials": {"username": "u", "password": "p"}},
    ).json()["id"]
    deployed = client.post(
        "/api/v1/instances",
        json={
            "template": _NAME,
            "param_values": {"window_from": _WINDOW_FROM, "window_to": _WINDOW_TO},
            "connections": {"source": src, "destination": dst},
        },
    )
    assert deployed.status_code == 200, deployed.text
    return deployed.json()["instance_id"]


_APPTS = {
    "a1": Appointment(ref="a1", start=_FUTURE, raw={"ref": "a1", "patient": "Müller", "status": "confirmed"}),
    "a2": Appointment(ref="a2", start=_FUTURE2, raw={"ref": "a2", "patient": "Schmidt", "status": "confirmed"}),
}


def test_import_creates_then_is_idempotent(monkeypatch):
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, _APPTS, dest_store)
    instance_id = _deploy()

    first = _import(instance_id)
    assert first["status"] == "completed"
    assert first["total"] == 2 and first["done"] == 2
    assert sorted(first["created"]) == ["a1", "a2"] and first["skipped"] == []
    assert first["complete"] is True
    assert set(dest_store) == {"a1", "a2"}  # actually appended to the destination

    # Re-run: everything already present -> all skipped, nothing created (append-only, idempotent).
    second = _import(instance_id)
    assert sorted(second["skipped"]) == ["a1", "a2"] and second["created"] == []


def test_import_can_shadow_into_task_timeline_without_changing_result(monkeypatch):
    monkeypatch.setattr(settings, "task_shadow_enabled", True)
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, _APPTS, dest_store)
    instance_id = _deploy()

    report = _import(instance_id)

    assert report["status"] == "completed"
    assert sorted(report["created"]) == ["a1", "a2"]
    task_id = report["task_id"]
    assert task_id is not None
    task = client.get(f"/api/v1/tasks/{task_id}")
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "completed"
    assert [event["kind"] for event in task.json()["events"]] == [
        "import_started",
        "import_completed",
    ]


def _booking(ref, start, name, birth_date, address, email) -> Appointment:
    """A source appointment shaped exactly like healthyfeet's admin payload: ONE composed `name`,
    a German `birth_date` (`formatBirthDate` renders the stored ISO date day-first), and the
    address composed into one line."""
    raw = {"ref": ref, "status": "confirmed", "name": name, "birth_date": birth_date,
           "address": address, "email": email, "service_label": "Nagelpflege"}
    return Appointment(ref=ref, start=start, patient=name, raw=raw)


def test_import_creates_one_patient_card_per_person(monkeypatch):
    """The point of ADR-0005, end to end through the studio's own API: two visits by the same
    person bind to ONE card, a different person gets their own, and the card carries exactly the
    practice's field list — with the source's German date and one-line address normalised on the
    way in."""
    appts = {
        "b1": _booking("b1", _FUTURE, "Valentina Zeller-Klaus", "26-01-1988",
                       "Teststr. 1, 80331 München", "v@example.com"),
        "b2": _booking("b2", _FUTURE2, "Valentina Zeller-Klaus", "26-01-1988",
                       "Teststr. 1, 80331 München", "v@example.com"),
        "b3": _booking("b3", _FUTURE2, "Bernd Anders", "03-03-1970",
                       "Am Hang 12a, 82031 Grünwald", "b@example.com"),
    }
    dest_store, cards, bound = {}, [], {}
    _install_connectors(monkeypatch, appts, dest_store, cards=cards, bound=bound)
    instance_id = _deploy()

    report = _import(instance_id)
    assert sorted(report["created"]) == ["b1", "b2", "b3"], report
    assert len(cards) == 2, f"two humans -> two cards, no duplicate for the second visit: {cards}"
    assert bound["b1"] == bound["b2"] != bound["b3"]  # both visits on the same card

    card = next(c for c in cards if c["nachname"] == "Zeller-Klaus")
    assert card["vorname"] == "Valentina"
    assert card["geburtsdatum"] == "1988-01-26"  # day-first in, ISO out
    assert (card["strasse"], card["plz"], card["ort"]) == ("Teststr. 1", "80331", "München")
    assert card["email"] == "v@example.com"

    # Re-running creates neither a second appointment nor a second card.
    second = _import(instance_id)
    assert sorted(second["skipped"]) == ["b1", "b2", "b3"] and second["created"] == []
    assert len(cards) == 2


def test_import_reports_silent_write_failure(monkeypatch):
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, _APPTS, dest_store, write_fails=True)
    instance_id = _deploy()

    report = _import(instance_id)
    # The job itself completes; a per-appointment write failure lands in `failed`, not a job crash.
    assert report["status"] == "completed"
    assert report["created"] == [] and len(report["failed"]) == 2
    assert all(f["status"] == "rejected" for f in report["failed"])


def test_import_excludes_unconfirmed_and_past(monkeypatch):
    """Safety filter (ADR-0004): only CONFIRMED, FUTURE bookings are imported. Cancelled,
    rescheduled, still-'new', and past appointments are excluded (reported, never written)."""
    appts = {
        "ok": Appointment(ref="ok", start=_FUTURE, raw={"ref": "ok", "status": "confirmed"}),
        "cancelled": Appointment(ref="cancelled", start=_FUTURE, raw={"ref": "cancelled", "status": "cancelled"}),
        "rescheduled": Appointment(ref="rescheduled", start=_FUTURE, raw={"ref": "rescheduled", "status": "rescheduled"}),
        "newish": Appointment(ref="newish", start=_FUTURE, raw={"ref": "newish", "status": "new"}),
        "past": Appointment(ref="past", start=_PAST, raw={"ref": "past", "status": "confirmed"}),
    }
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, appts, dest_store)
    instance_id = _deploy()

    report = _import(instance_id)
    assert report["total"] == 1  # only the one confirmed + future appointment is eligible
    assert report["created"] == ["ok"]
    assert set(dest_store) == {"ok"}  # nothing else ever reached the destination
    assert {x["ref"] for x in report["excluded"]} == {"cancelled", "rescheduled", "newish", "past"}
    assert report["complete"] is True


def test_import_concurrency_guard(monkeypatch):
    """A second trigger while a job is still running returns the SAME job — no duplicate run."""
    monkeypatch.setattr(instances_api, "spawn_import_job", lambda *a, **k: None)  # leave it running
    _install_connectors(monkeypatch, _APPTS, {})
    instance_id = _deploy()

    first = client.post(f"/api/v1/instances/{instance_id}/import")
    assert first.status_code == 202 and first.json()["status"] == "running"
    second = client.post(f"/api/v1/instances/{instance_id}/import")
    assert second.json()["job_id"] == first.json()["job_id"]


def test_connect_rejects_credentials_that_dont_authenticate(monkeypatch):
    """Connect-time validation: a credential that fails to log in is rejected with 400 and never
    persisted — so a bad thevea email fails in the connect form, not at import time."""

    class _BadAuth:
        def verify(self):
            raise RuntimeError("thevea graphql error: invalid email format")

        def close(self):
            pass

    monkeypatch.setattr(connectors_base, "build_connector", lambda *a, **k: _BadAuth())

    resp = client.post(
        "/api/v1/connections",
        json={"adapter": "thevea", "type": "calendar", "credentials": {"username": "nope", "password": "x"}},
    )
    assert resp.status_code == 400, resp.text
    assert "could not connect to thevea" in resp.json()["detail"]
    # Nothing was stored — the rejected connection must not linger.
    assert client.get("/api/v1/connections").json() == []
