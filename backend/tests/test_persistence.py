"""Stage 2 — runs and entities persist to Postgres and read back across sessions.

Talks to the real Supabase DB (ADR-0001). Skips cleanly if it's unreachable, and deletes every
row it creates so the live project stays clean. Each direct-DB helper uses its own short-lived
engine to avoid asyncpg's loop-bound-connection pitfalls when mixed with the TestClient loop.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import AgentInstance, EpisodeEvent, Run, Tenant
from app.db.repo import get_template_version
from app.db.seed import seed_templates_from_dir
from app.main import app

_RUNBOOKS = "./runbooks"


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    """Run an async DB op on a fresh engine (isolated from the app/TestClient engine)."""

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

_VALID_CASE = {
    "patient": {"id": "kvnr-x"},
    "consent": {"telephone_handling": True},
    "calendar": {"has_free_slot": True, "booking_confirmed": False},
}


def _seed():
    _run_db(lambda s: seed_templates_from_dir(s, _RUNBOOKS))


def _delete_run(run_id: str):
    rid = uuid.UUID(run_id)

    async def go(s: AsyncSession):
        await s.execute(delete(EpisodeEvent).where(EpisodeEvent.run_id == rid))
        await s.execute(delete(Run).where(Run.id == rid))
        await s.commit()

    _run_db(go)


def test_run_persists_and_reads_back() -> None:
    _seed()
    res = client.post(
        "/api/v1/runs", json={"runbook": "praxis_appointment", "case": _VALID_CASE}
    )
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    try:
        listed = client.get("/api/v1/runs").json()
        assert any(r["run_id"] == run_id for r in listed)

        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["status"] == "ok"
        # Ordered episode events reconstruct the step sequence (enforced steps only).
        assert [s["step_id"] for s in detail["steps"]] == ["verify_patient", "book_slot"]
    finally:
        _delete_run(run_id)


def test_unknown_template_returns_404() -> None:
    res = client.post("/api/v1/runs", json={"runbook": "does_not_exist", "case": {}})
    assert res.status_code == 404


def test_rejected_run_persists_and_reads_back() -> None:
    """The REJECT path end-to-end over HTTP: the tool claims success, the write never lands,
    the postcondition rejects the claim, and the rejected run is what persists."""
    _seed()
    case = {
        **_VALID_CASE,
        "calendar": {"has_free_slot": True, "booking_confirmed": False, "write_fails": True},
    }
    res = client.post("/api/v1/runs", json={"runbook": "praxis_appointment", "case": case})
    assert res.status_code == 200, res.text
    run_id = res.json()["run_id"]
    try:
        detail = client.get(f"/api/v1/runs/{run_id}").json()
        assert detail["status"] == "rejected"
        steps = {s["step_id"]: s for s in detail["steps"]}
        assert steps["book_slot"]["status"] == "rejected"
        assert steps["book_slot"]["expr"] == "calendar.booking_confirmed == true"
    finally:
        _delete_run(run_id)


def test_entities_survive_new_session() -> None:
    """A deployed instance written in one session is readable in a fresh one (restart proxy)."""
    _seed()
    tenant_id, instance_id = uuid.uuid4(), uuid.uuid4()

    async def write(s: AsyncSession):
        template = await get_template_version(s, "praxis_appointment", 1)
        assert template is not None
        s.add(Tenant(id=tenant_id, name="test-tenant"))
        await s.flush()  # ensure the tenant row exists before the instance FK references it
        s.add(
            AgentInstance(
                id=instance_id,
                tenant_id=tenant_id,
                template_id=template.id,
                template_version=1,
                status="deployed",
                param_values={"persona": "Dr. Test"},
            )
        )
        await s.commit()

    async def read_and_clean(s: AsyncSession):
        inst = await s.get(AgentInstance, instance_id)
        assert inst is not None and inst.status == "deployed"
        assert inst.param_values["persona"] == "Dr. Test"
        await s.delete(inst)
        tenant = await s.get(Tenant, tenant_id)
        await s.delete(tenant)
        await s.commit()

    _run_db(write)
    _run_db(read_and_clean)  # fresh engine + session == survives process boundary