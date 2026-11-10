"""Cross-session proof of durable intent and builder fencing, on disposable Postgres only."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.builder import service as builder
from app.db import repo
from app.db.models import BuilderRun, Definition, Tenant

URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL or urlsplit(URL).hostname not in ("127.0.0.1", "localhost"),
    reason="requires explicitly configured disposable local Postgres (CI)",
)


async def _setup(factory):
    async with factory() as session:
        tenant = Tenant(name=f"durability-{uuid.uuid4().hex}")
        session.add(tenant)
        await session.flush()
        definition = Definition(key=f"durability-{uuid.uuid4().hex}", module_key="demo", tenant_id=tenant.id)
        session.add(definition)
        await session.commit()
        return tenant, definition


def _run(fn):
    async def execute():
        engine = create_async_engine(URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            await fn(factory)
        finally:
            await engine.dispose()
    asyncio.run(execute())


def test_an_interrupted_run_keeps_intent_across_sessions_and_tenants_cannot_append():
    async def check(factory):
        tenant, _ = await _setup(factory)
        rid = uuid.uuid4()
        async with factory() as session:
            await repo.begin_run(session, run_id=rid, template_name="test", template_version=7,
                                 tenant_id=tenant.id, instance_id=None, case={"ref": "a-17"})
        async with factory() as session:
            await repo.append_run_event(session, run_id=rid, tenant_id=tenant.id,
                                        kind="action_intent", payload={"tool": "write", "action_key": "a-17"})
            await session.rollback()  # no completion: emulate the executing process disappearing
        async with factory() as session:
            run = await repo.get_run(session, rid, tenant_id=tenant.id)
            assert run.lifecycle == "unverified"
            assert [event.kind for event in run.events] == ["intent", "action_intent"]
            with pytest.raises(RuntimeError):
                await repo.append_run_event(session, run_id=rid, tenant_id=uuid.uuid4(), kind="trace", payload={})
        async with factory() as session:
            await repo.complete_run(session, run_id=rid, tenant_id=tenant.id, status="ok",
                                    trace_ref="test", step_payloads=[{"step_id": "write", "status": "ok"}])
        async with factory() as session:
            run = await repo.get_run(session, rid, tenant_id=tenant.id)
            assert run.lifecycle == "completed"
            assert [event.seq for event in run.events] == [0, 1, 2]
    _run(check)


def test_one_builder_claims_a_run_and_another_boot_respects_its_lease():
    async def check(factory):
        _, definition = await _setup(factory)
        async with factory() as session:
            run = await builder.start(session, definition, "Revise", [], "person")
            rid = run.id
        async with factory() as session:
            assert await repo.claim_builder_run(session, rid, "worker-a", 90)
        async with factory() as session:
            assert not await repo.claim_builder_run(session, rid, "worker-b", 90)
        await builder.interrupt_stale(factory)  # a new replica starting
        async with factory() as session:
            run = await session.get(BuilderRun, rid)
            assert run.state == "running" and run.owner_id == "worker-a"
            run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        await builder.interrupt_stale(factory)
        async with factory() as session:
            assert not await repo.renew_builder_lease(session, rid, "worker-a", 90)
            with pytest.raises(builder.LeaseLost):
                await builder.save_owned_draft(session, rid, "worker-a", definition, {})
            assert (await session.get(BuilderRun, rid)).state == "interrupted"
    _run(check)


def test_concurrent_builder_requests_cannot_both_queue_work():
    async def check(factory):
        _, definition = await _setup(factory)
        async def start():
            async with factory() as session:
                try:
                    await builder.start(session, definition, "Revise", [], "person")
                except builder.Busy:
                    return "busy"
                return "queued"
        assert sorted(await asyncio.gather(start(), start())) == ["busy", "queued"]
    _run(check)
