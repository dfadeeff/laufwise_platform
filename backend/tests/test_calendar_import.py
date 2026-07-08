"""End-to-end governed calendar import (ADR-0004): deploy a calendar_import instance bound to a
source + destination connection, then POST /import and check the completeness report — every
appointment is created, re-running skips them all (idempotent, append-only), and a silent
destination write failure is reported as failed. Driven through mock connectors (no network).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.connectors import base as connectors_base
from app.connectors.base import Appointment
from app.db.models import AgentInstance, Connection, EpisodeEvent, Run, Template, Tenant
from app.main import app

_NAME = "calendar_import_test"
_ORG = "org_calendar_import"
_KEY = Fernet.generate_key().decode()


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

    def close(self):
        pass


class _FakeDest:
    def __init__(self, store: dict[str, Appointment], write_fails: bool):
        self._store, self._write_fails = store, write_fails

    def find_appointment(self, ref):
        return self._store.get(ref)

    def create_appointment(self, appt):
        if not self._write_fails:  # silent failure = accept but never persist
            self._store[appt.ref] = appt

    def close(self):
        pass


def _install_connectors(monkeypatch, appts, dest_store, write_fails=False):
    def build(adapter, base_url, creds):
        if adapter == "healthyfeet":
            return _FakeSource(appts)
        if adapter == "thevea":
            return _FakeDest(dest_store, write_fails)
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
            "dest_match": {"provider": "destination", "query": "appointment_by_ref"},
        },
        "steps": [
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


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", _KEY)

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
            "param_values": {"window_from": "2026-07-01", "window_to": "2026-07-31"},
            "connections": {"source": src, "destination": dst},
        },
    )
    assert deployed.status_code == 200, deployed.text
    return deployed.json()["instance_id"]


_APPTS = {
    "a1": Appointment(ref="a1", start="2026-07-14T09:00", raw={"ref": "a1", "patient": "Müller"}),
    "a2": Appointment(ref="a2", start="2026-07-15T10:00", raw={"ref": "a2", "patient": "Schmidt"}),
}


def test_import_creates_then_is_idempotent(monkeypatch):
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, _APPTS, dest_store)
    instance_id = _deploy()

    first = client.post(f"/api/v1/instances/{instance_id}/import").json()
    assert first["total"] == 2
    assert sorted(first["created"]) == ["a1", "a2"] and first["skipped"] == []
    assert first["complete"] is True
    assert set(dest_store) == {"a1", "a2"}  # actually appended to the destination

    # Re-run: everything already present -> all skipped, nothing created (append-only, idempotent).
    second = client.post(f"/api/v1/instances/{instance_id}/import").json()
    assert sorted(second["skipped"]) == ["a1", "a2"] and second["created"] == []


def test_import_reports_silent_write_failure(monkeypatch):
    dest_store: dict[str, Appointment] = {}
    _install_connectors(monkeypatch, _APPTS, dest_store, write_fails=True)
    instance_id = _deploy()

    report = client.post(f"/api/v1/instances/{instance_id}/import").json()
    assert report["created"] == [] and len(report["failed"]) == 2
    assert all(f["status"] == "rejected" for f in report["failed"])