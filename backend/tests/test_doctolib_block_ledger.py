"""The block mirror's ledger against Postgres (ADR-0044): the contract's thread records and reads
blocks through the run's own event loop, each write committed at once, scoped to one workspace's
one doctolib connection."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from app.connectors.base import Block
from app.db import repo
from app.db.models import Connection, DoctolibBlock, Tenant
from app.sync.block_ledger import DbBlockLedger
from tests.test_schedule_worker import _sessionmaker
from tests.test_workflow_agents import _db_reachable

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
ORG = "org_test_doctolib_blocks"
DAY = "2027-03-01"
B1 = Block("2570190", "2027-03-01T07:00:00.000Z", "2027-03-01T08:00:00.000Z")
B2 = Block("2557171", "2027-03-01T09:00:00.000Z", "2027-03-01T09:30:00.000Z")


async def _cleanup(sm) -> None:
    async with sm() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.clerk_org_id == ORG))).scalar_one_or_none()
        if tenant is not None:
            await s.execute(delete(DoctolibBlock).where(DoctolibBlock.tenant_id == tenant.id))
            await s.execute(delete(Connection).where(Connection.tenant_id == tenant.id))
        await s.commit()


@db
def test_the_ledger_keeps_only_its_live_blocks_for_the_day():
    async def scenario():
        sm = _sessionmaker()
        await _cleanup(sm)
        async with sm() as s:
            tenant = await repo.tenant_for_org(s, ORG, name=ORG)
            mine = Connection(tenant_id=tenant.id, type="calendar", adapter="doctolib", config={})
            other = Connection(tenant_id=tenant.id, type="calendar", adapter="doctolib", config={})
            s.add_all([mine, other])
            await s.commit()
        loop = asyncio.get_running_loop()
        ledger = DbBlockLedger(tenant.id, mine.id, loop)
        elsewhere = DbBlockLedger(tenant.id, other.id, loop)

        def in_the_contracts_thread():
            ledger.record(DAY, "11", B1)
            ledger.record(DAY, "12", B2)
            ledger.record("2027-03-02", "13", B1)
            elsewhere.record(DAY, "21", B1)
            ledger.forget("12")
            return ledger.owned(DAY)

        try:
            assert await asyncio.to_thread(in_the_contracts_thread) == [("11", B1)]
            async with sm() as s:
                gone = (await s.execute(select(DoctolibBlock).where(DoctolibBlock.event_id == "12"))).scalar_one()
                assert gone.removed_at is not None  # kept as the record of what it did
        finally:
            await _cleanup(sm)

    asyncio.run(scenario())
