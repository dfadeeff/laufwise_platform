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
from app.db import repo
from app.db.models import (
    AgentInstance,
    Connection,
    EpisodeEvent,
    InstanceConnection,
    Run,
    Tenant,
)
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

def test_a_voice_call_timeline_persists_and_reads_back_in_order() -> None:
    """A live call is written down turn by turn and can be read back afterwards.

    This is the tier where a model decides what to say, so an unrecorded decision may as well not
    have happened. Covers the whole path the Studio uses: resolve the instance a conversation
    belongs to, append an ordered timeline, close it.
    """
    tenant_id = uuid.uuid4()
    holder: dict[str, uuid.UUID] = {}

    async def write(s: AsyncSession):
        await seed_templates_from_dir(s, _RUNBOOKS)
        s.add(Tenant(id=tenant_id, name="voice-timeline-tenant"))
        await s.flush()
        instance = await repo.studio_voice_instance(
            s, tenant_id=tenant_id, template_name="voice_appointment"
        )
        assert instance is not None, "voice_appointment must be published for the Studio to run"
        # Pinned to a version, which is what lets a saved call say which agent produced it.
        assert instance.template_version >= 1
        conversation = await repo.create_conversation(
            s,
            tenant_id=tenant_id,
            instance_id=instance.id,
            channel="voice",
            direction="inbound",
            metadata={"surface": "studio", "language": "de"},
        )
        holder["conversation"] = conversation.id
        holder["instance"] = instance.id
        for kind, payload in (
            ("turn", {"role": "caller", "text": "Ich bräuchte einen Termin."}),
            ("turn", {"role": "agent", "text": "Gerne. Wie ist Ihr Vorname?"}),
            ("tool_call", {"tool": "appointment_book", "arguments": {},
                           "result": {"status": "blocked"}, "run_id": "r-1"}),
        ):
            await repo.append_conversation_event(
                s, conversation_id=conversation.id, kind=kind, payload=payload
            )
        await repo.end_conversation(s, conversation_id=conversation.id, status="completed")

    async def read_and_clean(s: AsyncSession):
        conversation = await repo.get_conversation(s, holder["conversation"], tenant_id)
        assert conversation is not None
        assert conversation.status == "completed" and conversation.ended_at is not None
        # seq is assigned from what is stored, so the timeline reads back in the order it happened.
        assert [event.seq for event in conversation.events] == [0, 1, 2]
        assert [event.kind for event in conversation.events] == ["turn", "turn", "tool_call"]
        assert conversation.events[2].payload["run_id"] == "r-1"
        assert conversation.events[0].payload["text"] == "Ich bräuchte einen Termin."

        for event in conversation.events:
            await s.delete(event)
        await s.delete(conversation)
        await s.execute(
            delete(InstanceConnection).where(
                InstanceConnection.instance_id == holder["instance"]
            )
        )
        await s.execute(delete(AgentInstance).where(AgentInstance.id == holder["instance"]))
        await s.execute(delete(Connection).where(Connection.tenant_id == tenant_id))
        await s.delete(await s.get(Tenant, tenant_id))
        await s.commit()

    _run_db(write)
    _run_db(read_and_clean)


def test_the_studio_reuses_one_instance_rather_than_deploying_per_call() -> None:
    """Every call would otherwise mint a new instance, and comparing versions would be hopeless."""
    tenant_id = uuid.uuid4()
    seen: dict[str, uuid.UUID] = {}

    async def resolve_twice(s: AsyncSession):
        await seed_templates_from_dir(s, _RUNBOOKS)
        s.add(Tenant(id=tenant_id, name="voice-instance-tenant"))
        await s.flush()
        first = await repo.studio_voice_instance(
            s, tenant_id=tenant_id, template_name="voice_appointment"
        )
        second = await repo.studio_voice_instance(
            s, tenant_id=tenant_id, template_name="voice_appointment"
        )
        assert first is not None and second is not None
        assert first.id == second.id
        seen["instance"] = first.id

    async def clean(s: AsyncSession):
        await s.execute(
            delete(InstanceConnection).where(InstanceConnection.instance_id == seen["instance"])
        )
        await s.execute(delete(AgentInstance).where(AgentInstance.id == seen["instance"]))
        await s.execute(delete(Connection).where(Connection.tenant_id == tenant_id))
        await s.delete(await s.get(Tenant, tenant_id))
        await s.commit()

    _run_db(resolve_twice)
    _run_db(clean)


def test_an_inbound_number_resolves_to_the_agent_that_answers_it() -> None:
    """The dialled number is the only routing key an inbound call carries.

    A paused or draft instance must not answer, or pausing an agent would not actually take it
    off the phone.
    """
    tenant_id = uuid.uuid4()
    number = f"+4915100{uuid.uuid4().int % 1000000:06d}"
    made: dict[str, uuid.UUID] = {}

    async def write(s: AsyncSession):
        await seed_templates_from_dir(s, _RUNBOOKS)
        s.add(Tenant(id=tenant_id, name="telephony-tenant"))
        await s.flush()
        template = await get_template_version(s, "voice_appointment", 1)
        assert template is not None
        for status, phone in (("deployed", number), ("paused", number + "9")):
            instance = AgentInstance(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                template_id=template.id,
                template_version=1,
                status=status,
                phone_number=phone,
            )
            s.add(instance)
            made[status] = instance.id
        await s.commit()

    async def read_and_clean(s: AsyncSession):
        found = await repo.instance_for_phone_number(s, phone_number=number)
        assert found is not None and found.id == made["deployed"]
        assert found.tenant_id == tenant_id, "the number decides which tenant owns the call"

        paused = await repo.instance_for_phone_number(s, phone_number=number + "9")
        assert paused is None, "a paused agent must not answer the phone"
        assert await repo.instance_for_phone_number(s, phone_number="+490000000") is None

        await s.execute(delete(AgentInstance).where(AgentInstance.tenant_id == tenant_id))
        await s.delete(await s.get(Tenant, tenant_id))
        await s.commit()

    _run_db(write)
    _run_db(read_and_clean)
