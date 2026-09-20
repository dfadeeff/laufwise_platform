"""Arming an instance for the backend clock (ADR-0010 D3, criterion 7).

The column existed from the start; the way to set it did not, so moving a schedule onto a
redeployed instance meant editing production by hand — and a template version bump forces exactly
that redeploy. These cover what the endpoint has to get right: only a schedule a process runs,
only an instance a clock can drive, and the schedule MOVES rather than multiplies.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.db.models import AgentInstance, Connection, ImportJob, Template, Tenant
from app.main import app
from app.sync.scheduler import SCHEDULE

_ORG = "org_arming_test"
_VOICE = "arming_test_voice"


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
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


def _as_org():
    async def _dep() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                tenant = await repo.tenant_for_org(s, _ORG, name=_ORG)
                await s.commit()
                return tenant
        finally:
            await engine.dispose()

    return _dep


def _publish_voice_template() -> None:
    """A template no clock can drive — `_ORCHESTRATORS` does not know it."""
    contract = {
        "template": _VOICE,
        "risk": "low",
        "agent_class": "conversational",
        "parameters": {"persona": {"type": "text", "required": True}},
        "required_connections": [],
        "state": {"calendar": {"provider": "memory"}},
        "steps": [
            {
                "id": "book_slot",
                "kind": "enforced",
                "tools": ["book_appointment"],
                "preconditions": [{"check": "calendar.has_free_slot == true"}],
                "execute": {"adapter": "registry", "tool": "book_appointment"},
                "postconditions": [{"check": "calendar.booking_confirmed == true"}],
            },
        ],
    }
    client.post("/api/v1/templates", json={"contract": contract})
    client.post(f"/api/v1/templates/{_VOICE}/publish")


def _deploy(template: str, params: dict) -> str:
    resp = client.post(
        "/api/v1/instances", json={"template": template, "param_values": params}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["instance_id"]


def _mirror_instance() -> str:
    return _deploy(
        "availability_mirror",
        {"window_from": "2026-09-21", "window_to": "2026-09-28", "rooms": "208413"},
    )


def _cleanup() -> None:
    async def go(s: AsyncSession):
        tenant = (
            await s.execute(select(Tenant).where(Tenant.clerk_org_id == _ORG))
        ).scalar_one_or_none()
        if tenant is not None:
            await s.execute(delete(ImportJob).where(ImportJob.tenant_id == tenant.id))
            for instance in (
                await s.execute(
                    select(AgentInstance).where(AgentInstance.tenant_id == tenant.id)
                )
            ).scalars():
                await s.delete(instance)
            await s.flush()
            # The deploy creates the tenant's simulated connection; the tenant row cannot go
            # while it references back.
            await s.execute(delete(Connection).where(Connection.tenant_id == tenant.id))
            await s.delete(tenant)
        await s.execute(delete(Template).where(Template.name == _VOICE))
        await s.commit()

    _run_db(go)


@pytest.fixture(autouse=True)
def _isolated():
    _cleanup()
    app.dependency_overrides[current_tenant] = _as_org()
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _set_schedule(instance_id: str, schedule: str | None):
    return client.put(f"/api/v1/instances/{instance_id}/schedule", json={"schedule": schedule})


# --- what the clock then picks up -----------------------------------------------------------


def test_arming_makes_the_scheduler_see_the_instance_and_disarming_hides_it_again():
    instance_id = _mirror_instance()

    armed = _set_schedule(instance_id, SCHEDULE)
    assert armed.status_code == 200, armed.text
    assert armed.json()["schedule"] == SCHEDULE
    seen = _run_db(lambda s: repo.scheduled_instances(s, SCHEDULE))
    assert instance_id in {i.id.hex for i in seen}

    disarmed = _set_schedule(instance_id, None)
    assert disarmed.status_code == 200
    assert disarmed.json()["schedule"] is None
    seen = _run_db(lambda s: repo.scheduled_instances(s, SCHEDULE))
    assert instance_id not in {i.id.hex for i in seen}


def test_the_schedule_moves_to_the_new_instance_instead_of_running_both():
    """The case this exists for: a template version bump means a redeploy, and the old instance
    still carries the parameters the redeploy replaced. Two armed instances would sweep the same
    days twice and the stale one would keep publishing — silently, looking like the new settings
    did nothing."""
    old = _mirror_instance()
    _set_schedule(old, SCHEDULE)
    new = _mirror_instance()

    _set_schedule(new, SCHEDULE)

    armed = {i.id.hex for i in _run_db(lambda s: repo.scheduled_instances(s, SCHEDULE))}
    assert new in armed
    assert old not in armed


def test_arming_the_same_instance_twice_is_a_no_op_rather_than_a_self_disarm():
    instance_id = _mirror_instance()
    _set_schedule(instance_id, SCHEDULE)

    again = _set_schedule(instance_id, SCHEDULE)

    assert again.json()["schedule"] == SCHEDULE
    armed = {i.id.hex for i in _run_db(lambda s: repo.scheduled_instances(s, SCHEDULE))}
    assert instance_id in armed


# --- what it refuses ------------------------------------------------------------------------


def test_a_schedule_no_process_runs_is_refused_rather_than_stored():
    """A stored name nobody fires is the worst outcome available here: the instance reads as
    armed and never runs."""
    instance_id = _mirror_instance()

    refused = _set_schedule(instance_id, "hourly")

    assert refused.status_code == 422
    assert "hourly" in refused.text
    assert _run_db(lambda s: repo.scheduled_instances(s, "hourly")) == []


def test_a_paused_instance_cannot_be_armed():
    """`paused` already disarms (the scheduler reads deployed only), so arming one would be a
    promise nothing keeps."""
    instance_id = _mirror_instance()
    client.post(f"/api/v1/instances/{instance_id}/pause")

    refused = _set_schedule(instance_id, SCHEDULE)

    assert refused.status_code == 409
    assert "paused" in refused.text


def test_a_template_no_orchestrator_can_drive_is_refused():
    """Arming a voice agent would hand it to the import orchestrator and walk a work-list that
    does not exist for it."""
    _publish_voice_template()
    instance_id = _deploy(_VOICE, {"persona": "freundlich"})

    refused = _set_schedule(instance_id, SCHEDULE)

    assert refused.status_code == 422
    assert "availability_mirror" in refused.text


def test_another_tenants_instance_is_a_404_not_a_disarm():
    """Tenant isolation is on by default (ADR-0003 D2): an unowned id is not found, and nothing
    about the other tenant's arming changes."""
    instance_id = _mirror_instance()
    _set_schedule(instance_id, SCHEDULE)

    async def _other() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                tenant = await repo.tenant_for_org(s, _ORG + "_b", name=_ORG + "_b")
                await s.commit()
                return tenant
        finally:
            await engine.dispose()

    app.dependency_overrides[current_tenant] = _other
    try:
        assert _set_schedule(instance_id, None).status_code == 404
    finally:
        app.dependency_overrides[current_tenant] = _as_org()
        _run_db(
            lambda s: s.execute(delete(Tenant).where(Tenant.clerk_org_id == _ORG + "_b"))
        )

    still_armed = {i.id.hex for i in _run_db(lambda s: repo.scheduled_instances(s, SCHEDULE))}
    assert instance_id in still_armed
