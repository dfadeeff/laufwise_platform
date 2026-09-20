"""The backend clock for the occupancy mirror (ADR-0010).

What actually has to hold: the window follows today rather than a stored date, the horizon tier
really covers the whole booking horizon, an orphaned job cannot wedge the schedule, and a run the
clock starts is distinguishable from one a person started. The governed loop itself is not
re-tested here — the clock decides WHEN, never what may be written.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import repo
from app.db.models import AgentInstance, ImportJob, Template, Tenant
from app.sync import scheduler
from app.sync.mirror import MAX_DAYS, _days

_ORG = "org_scheduler_test"


# --- the window: a fact about today, not about the day somebody typed it ------------------

def test_each_tier_starts_today_and_spans_its_own_reach():
    today = date(2026, 9, 20)
    near = scheduler.window_for("near", today)
    horizon = scheduler.window_for("horizon", today)

    assert near == {"from": "2026-09-20", "to": "2026-09-27"}          # today + 7
    assert horizon["from"] == "2026-09-20"
    assert horizon["to"] == (today + timedelta(days=MAX_DAYS - 1)).isoformat()


def test_the_horizon_tier_is_not_silently_truncated_by_the_orchestrator():
    """The orchestrator caps a run at MAX_DAYS. A tier asking for more would be cut without
    anyone noticing — and the cut falls on the far edge, which is the part nobody checks."""
    window = scheduler.window_for("horizon", date(2026, 9, 20))
    days = _days(window)
    # The last day walked IS the last day asked for — nothing fell off the end.
    assert days[-1] == window["to"]
    assert len(days) == MAX_DAYS


def test_the_horizon_covers_what_the_website_actually_sells():
    """The site offers BOOKING_HORIZON_MONTHS = 2 (~62 days). A sweep shorter than that leaves
    days reaching the booking window having never been mirrored."""
    assert scheduler.TIERS["horizon"] > 62


def test_an_unknown_tier_is_refused_rather_than_guessed():
    assert scheduler.main([]) == 2
    assert scheduler.main(["hourly"]) == 2


# --- everything below needs a database ----------------------------------------------------

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


db = pytest.mark.skipif(not _db_reachable(), reason="database not reachable")


async def _seed(s: AsyncSession) -> dict[str, Any]:
    """A tenant with three instances: armed, armed-but-paused, and not armed."""
    await _wipe(s)
    tenant = Tenant(name="scheduler test", clerk_org_id=_ORG)
    template = Template(
        name=f"availability_mirror_test_{uuid.uuid4().hex[:8]}",
        version=1,
        agent_class="workflow",
        status="published",
        contract={"steps": []},
        parameters={},
    )
    s.add_all([tenant, template])
    await s.flush()

    made = {}
    for key, status, sched in (
        ("armed", "deployed", scheduler.SCHEDULE),
        ("paused", "paused", scheduler.SCHEDULE),
        ("manual", "deployed", None),
    ):
        inst = AgentInstance(
            tenant_id=tenant.id, template_id=template.id, template_version=1,
            param_values={}, status=status, schedule=sched,
        )
        s.add(inst)
        made[key] = inst
    await s.flush()
    await s.commit()
    return {"tenant": tenant, "template": template, **made}


async def _wipe(s: AsyncSession) -> None:
    tenant = (await s.execute(select(Tenant).where(Tenant.clerk_org_id == _ORG))).scalars().first()
    if tenant is None:
        return
    insts = (await s.execute(select(AgentInstance).where(AgentInstance.tenant_id == tenant.id))).scalars().all()
    for inst in insts:
        await s.execute(delete(ImportJob).where(ImportJob.instance_id == inst.id))
    await s.execute(delete(AgentInstance).where(AgentInstance.tenant_id == tenant.id))
    await s.execute(delete(Tenant).where(Tenant.id == tenant.id))
    await s.commit()


@db
def test_only_deployed_and_armed_instances_are_fired():
    """`paused` disarms — that is why the schedule is one column and not two. An instance that
    was never armed is never touched by the clock."""
    async def go(s: AsyncSession):
        made = await _seed(s)
        found = await repo.scheduled_instances(s, scheduler.SCHEDULE)
        ids = {i.id for i in found}
        assert made["armed"].id in ids
        assert made["paused"].id not in ids
        assert made["manual"].id not in ids
        await _wipe(s)

    _run_db(go)


@db
def test_a_job_orphaned_by_a_restart_is_reclaimed_not_left_running():
    """A process restart leaves a job `running` forever. With the concurrency guard, one orphan
    would block this instance's every later tick — silently, since the only symptom is a website
    going stale. A job that is merely slow but still reporting progress must survive."""
    async def go(s: AsyncSession):
        made = await _seed(s)
        stale = await repo.create_import_job(
            s, tenant_id=made["tenant"].id, instance_id=made["armed"].id
        )
        live = await repo.create_import_job(
            s, tenant_id=made["tenant"].id, instance_id=made["manual"].id
        )
        await s.commit()
        # Only the first has gone quiet.
        stale.updated_at = datetime.now(timezone.utc) - timedelta(
            minutes=scheduler.STALE_AFTER_MIN + 5
        )
        await s.commit()

        reclaimed = await repo.reclaim_stale_import_jobs(
            s, older_than_minutes=scheduler.STALE_AFTER_MIN
        )
        await s.refresh(stale)
        await s.refresh(live)

        assert reclaimed == 1
        assert stale.status == "interrupted"
        assert live.status == "running"
        # And the guard is free again for that instance.
        assert await repo.running_import_job_for_instance(
            s, made["armed"].id, made["tenant"].id
        ) is None
        await _wipe(s)

    _run_db(go)


@db
def test_a_scheduled_run_is_labelled_and_a_busy_instance_is_skipped(monkeypatch):
    """Two things the record has to answer: did the clock fire, and did it double-run anything.
    The worker is stubbed — what the run DOES is the governed loop's business, tested elsewhere."""
    calls: list[tuple[uuid.UUID, dict]] = []

    async def fake_worker(job_id, instance_id, tenant_id, window):
        calls.append((instance_id, window))

    monkeypatch.setattr(scheduler, "execute_import_job", fake_worker)

    async def go(s: AsyncSession):
        made = await _seed(s)
        await _wipe_jobs(s, made)
        return made

    made = _run_db(go)
    started = asyncio.run(scheduler.run_tier("near"))

    assert started == 1
    assert calls and calls[0][0] == made["armed"].id
    assert calls[0][1] == scheduler.window_for("near", date.today())

    def check(s: AsyncSession):
        async def inner(s: AsyncSession):
            job = (await s.execute(
                select(ImportJob).where(ImportJob.instance_id == made["armed"].id)
            )).scalars().first()
            assert job is not None and job.trigger == "schedule"
            # Leave it running: the next tier must not start a second run over the same days.
            job.status = "running"
            await s.commit()
        return inner(s)

    _run_db(check)
    calls.clear()
    again = asyncio.run(scheduler.run_tier("near"))
    assert again == 0 and calls == []

    _run_db(_wipe)


async def _wipe_jobs(s: AsyncSession, made: dict[str, Any]) -> None:
    for key in ("armed", "paused", "manual"):
        await s.execute(delete(ImportJob).where(ImportJob.instance_id == made[key].id))
    await s.commit()
