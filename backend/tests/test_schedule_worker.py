"""The scheduler's worker (ADR-0040 D5), against Postgres: a due schedule becomes one occurrence,
the occurrence becomes one governed case run, and nothing fires twice, late without saying so,
for someone who may no longer schedule, or across workspaces.

The worker is given its clock (`now`) and the admin check (`authority`), so every test states
exactly when it is and who is still an administrator."""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import repo
from app.db.models import (
    AgentSchedule,
    CaseRun,
    Definition,
    DefinitionVersion,
    ScheduleOccurrence,
    Tenant,
    WorkflowAgent,
    WorkflowCase,
)
from app.kernel.definitions import DefinitionError, checksum, read_definition, workflow_from
from app.schedules import service, worker
from tests.conftest import FIXTURE_DEFINITIONS
from tests.test_letters import SETTINGS
from tests.test_workflow_agents import _db_reachable, _fixture

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
UTC = timezone.utc
THREE_A_DAY = {"kind": "times", "days": [1, 2, 3, 4, 5], "at": ["08:00", "13:00", "18:00"]}
# Monday 9 November 2026 in Berlin (CET, UTC+1).
MON_0800 = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
MON_1300 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
MON_1800 = datetime(2026, 11, 9, 17, 0, tzinfo=UTC)
TUE_0800 = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
_ORGS = ("org_test_sched_a", "org_test_sched_b")


def _authority(answer):
    async def check(_org, _user):
        return answer

    return check


async def _yes(_org, _user) -> bool:
    return True


# --- the definition says whether it may be scheduled ------------------------------------------


def _content(triggers=("manual", "schedule")) -> dict:
    module = FIXTURE_DEFINITIONS / "demo"
    content = read_definition(module / "umlage", module)
    content = copy.deepcopy(content)
    if triggers is not None:
        content["triggers"] = list(triggers)
    return content


def test_a_definition_that_allows_a_schedule_is_schedulable(tmp_path) -> None:
    assert workflow_from(_content(), tmp_path).schedulable
    assert not workflow_from(_content(triggers=None), tmp_path).schedulable  # manual by default
    assert not workflow_from(_content(triggers=["manual"]), tmp_path).schedulable


@pytest.mark.parametrize("triggers", [[], ["webhook"], ["schedule", "cron"], "schedule"])
def test_an_unknown_trigger_is_refused(tmp_path, triggers) -> None:
    content = _content()
    content["triggers"] = triggers
    with pytest.raises(DefinitionError):
        workflow_from(content, tmp_path)


# --- the database -----------------------------------------------------------------------------


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(settings.sqlalchemy_url, poolclass=NullPool), expire_on_commit=False
    )


async def _cleanup(sm) -> None:
    async with sm() as s:
        for org in _ORGS:
            tenant = (await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))).scalar_one_or_none()
            if tenant is None:
                continue
            await s.execute(delete(ScheduleOccurrence).where(ScheduleOccurrence.tenant_id == tenant.id))
            await s.execute(delete(AgentSchedule).where(AgentSchedule.tenant_id == tenant.id))
            await s.execute(delete(WorkflowCase).where(WorkflowCase.tenant_id == tenant.id))
            await s.execute(delete(WorkflowAgent).where(WorkflowAgent.tenant_id == tenant.id))
            # A published version can never be deleted (the database refuses it), so the
            # workspace and its definitions stay, as in the other definition tests; drafts go.
            definitions = select(Definition.id).where(Definition.tenant_id == tenant.id)
            await s.execute(
                delete(DefinitionVersion).where(
                    DefinitionVersion.definition_id.in_(definitions), DefinitionVersion.status == "draft"
                )
            )
        await s.commit()


async def _workspace(sm, org: str, triggers=("manual", "schedule"), status="published") -> dict:
    """A workspace running the demo module, a definition of its own with one version, and an
    agent of it. Returns the ids a schedule needs."""
    async with sm() as s:
        tenant = await repo.tenant_for_org(s, org, name=org)
        await repo.add_workspace_module(s, tenant.id, "demo")
        key = f"sched-{uuid.uuid4().hex[:8]}"
        content = _content(triggers)
        content["key"] = key
        definition = Definition(key=key, module_key="demo", tenant_id=tenant.id)
        s.add(definition)
        await s.flush()
        version = DefinitionVersion(
            definition_id=definition.id, version=1, status=status, content=content,
            checksum=checksum(content),
            published_at=datetime.now(UTC) if status == "published" else None,
        )
        agent = WorkflowAgent(
            tenant_id=tenant.id, module_key="demo", workflow_key=key, name="Umlagen", settings=SETTINGS
        )
        s.add_all([version, agent])
        await s.commit()
        return {"tenant": tenant.id, "agent": agent.id, "version": version.id}


async def _schedule(sm, ws: dict, *, next_run_at=MON_0800, enabled=True, target=None, **kw) -> uuid.UUID:
    async with sm() as s:
        schedule = AgentSchedule(
            tenant_id=ws["tenant"], target_kind="workflow", target_id=target or ws["agent"],
            target_version_id=ws["version"], name="Morgens", rule=THREE_A_DAY,
            timezone="Europe/Berlin", input=_fixture("u1"), enabled=enabled,
            next_run_at=next_run_at if enabled else None, created_by="user_alice",
            updated_by="user_alice", run_as="user_alice", **kw,
        )
        s.add(schedule)
        await s.commit()
        return schedule.id


async def _occurrences(sm, schedule_id) -> list[ScheduleOccurrence]:
    async with sm() as s:
        stmt = (
            select(ScheduleOccurrence)
            .where(ScheduleOccurrence.schedule_id == schedule_id)
            .order_by(ScheduleOccurrence.scheduled_for)
        )
        return list((await s.execute(stmt)).scalars())


async def _get(sm, model, id_):
    async with sm() as s:
        return await s.get(model, id_)


async def _cases(sm, tenant_id) -> list[WorkflowCase]:
    async with sm() as s:
        return list((await s.execute(select(WorkflowCase).where(WorkflowCase.tenant_id == tenant_id))).scalars())


@pytest.fixture
def sm():
    """A clean slate for the two test workspaces, before and after."""
    maker = _sessionmaker()
    asyncio.run(_cleanup(maker))
    yield maker
    asyncio.run(_cleanup(maker))


def _tick(sm, now, node="node-a", authority=_yes):
    return asyncio.run(worker.tick(sm, node, now=now, authority=authority))


@db
def test_a_due_schedule_runs_its_agent_once_as_a_governed_case_run(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert (occ.status, occ.scheduled_for, occ.attempt) == ("succeeded", MON_0800, 1)
    [case] = asyncio.run(_cases(sm, ws["tenant"]))
    assert occ.run_ref == f"case:{case.id}" and occ.outcome == case.status == "ready"
    assert case.title == "Morgens · 09.11.2026 08:00"
    assert case.definition_version_id == ws["version"]  # the version it was set to run
    async def run_of(s):
        return (await s.execute(select(CaseRun).where(CaseRun.case_id == case.id))).scalar_one()
    async def go():
        async with sm() as s:
            return await run_of(s)
    assert asyncio.run(go()).trigger == "schedule"
    assert asyncio.run(_get(sm, AgentSchedule, sid)).next_run_at == MON_1300

    _tick(sm, MON_0800 + timedelta(seconds=40))  # the next tick: nothing is due
    assert len(asyncio.run(_occurrences(sm, sid))) == 1


@db
def test_two_workers_at_the_same_moment_run_it_once(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))
    now = MON_0800 + timedelta(seconds=5)

    async def both():
        await asyncio.gather(
            worker.tick(sm, "node-a", now=now, authority=_yes),
            worker.tick(sm, "node-b", now=now, authority=_yes),
        )

    asyncio.run(both())

    assert [o.status for o in asyncio.run(_occurrences(sm, sid))] == ["succeeded"]
    assert len(asyncio.run(_cases(sm, ws["tenant"]))) == 1


@db
def test_after_downtime_the_missed_runs_collapse_into_one_and_say_so(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_1800 + timedelta(minutes=10))  # down from before 08:00 until 18:10

    missed, ran = asyncio.run(_occurrences(sm, sid))
    assert (missed.status, missed.scheduled_for) == ("skipped_missed", MON_0800)
    assert "2" in missed.error and "18:00" in missed.error
    assert (ran.status, ran.scheduled_for) == ("succeeded", MON_1800)
    assert len(asyncio.run(_cases(sm, ws["tenant"]))) == 1
    assert asyncio.run(_get(sm, AgentSchedule, sid)).next_run_at == TUE_0800


@db
def test_pausing_cancels_what_has_not_started(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    async def enqueue_then_pause():
        async with sm() as s:
            await worker.enqueue_due(s, MON_0800 + timedelta(seconds=1))
            await s.commit()
        async with sm() as s:
            schedule = await repo.get_schedule(s, sid, ws["tenant"], lock=True)
            await service.pause(s, schedule, by="user_bob")
            await s.commit()

    asyncio.run(enqueue_then_pause())
    _tick(sm, MON_0800 + timedelta(seconds=30))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "cancelled"
    schedule = asyncio.run(_get(sm, AgentSchedule, sid))
    assert (schedule.enabled, schedule.next_run_at, schedule.updated_by) == (False, None, "user_bob")
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_resuming_runs_from_the_next_time_not_the_missed_ones(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws, enabled=False))

    async def resume():
        async with sm() as s:
            schedule = await repo.get_schedule(s, sid, ws["tenant"], lock=True)
            await service.resume(s, schedule, by="user_alice", now=MON_0800 + timedelta(hours=2))
            await s.commit()

    asyncio.run(resume())
    schedule = asyncio.run(_get(sm, AgentSchedule, sid))
    assert (schedule.enabled, schedule.next_run_at) == (True, MON_1300)


@db
def test_a_creator_who_is_no_longer_an_admin_pauses_the_schedule(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_0800 + timedelta(seconds=10), authority=_authority(False))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "administrator" in occ.error
    schedule = asyncio.run(_get(sm, AgentSchedule, sid))
    assert (schedule.enabled, schedule.next_run_at) == (False, None)
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_an_admin_who_resumes_it_takes_it_over_from_a_creator_who_left(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    async def only_bob(_org, user):
        return user == "user_bob"

    _tick(sm, MON_0800 + timedelta(seconds=10), authority=only_bob)  # alice left: paused

    async def bob_resumes():
        async with sm() as s:
            schedule = await repo.get_schedule(s, sid, ws["tenant"], lock=True)
            await service.resume(s, schedule, by="user_bob", now=MON_0800 + timedelta(minutes=1))
            await s.commit()

    asyncio.run(bob_resumes())
    _tick(sm, MON_1300 + timedelta(seconds=10), authority=only_bob)

    occs = asyncio.run(_occurrences(sm, sid))
    assert [o.status for o in occs] == ["failed", "succeeded"]
    schedule = asyncio.run(_get(sm, AgentSchedule, sid))
    assert (schedule.enabled, schedule.run_as, schedule.created_by) == (True, "user_bob", "user_alice")


@db
def test_when_nobody_can_say_who_is_an_admin_the_run_fails_and_the_schedule_stays(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_0800 + timedelta(seconds=10), authority=_authority(None))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "confirm" in occ.error
    schedule = asyncio.run(_get(sm, AgentSchedule, sid))
    assert (schedule.enabled, schedule.next_run_at) == (True, MON_1300)
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_an_agent_already_running_on_a_schedule_is_skipped_not_doubled(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    first = asyncio.run(_schedule(sm, ws))
    second = asyncio.run(_schedule(sm, ws, next_run_at=MON_0800))

    async def held_by_a_live_worker():
        async with sm() as s:
            await worker.enqueue_due(s, MON_0800 + timedelta(seconds=1))
            occ = (await s.execute(
                select(ScheduleOccurrence).where(ScheduleOccurrence.schedule_id == first)
            )).scalar_one()
            occ.status, occ.locked_by = "claimed", "node-live"
            occ.locked_until, occ.attempt = MON_0800 + timedelta(minutes=5), 1
            await s.commit()

    asyncio.run(held_by_a_live_worker())
    _tick(sm, MON_0800 + timedelta(seconds=30))

    [occ] = asyncio.run(_occurrences(sm, second))
    assert occ.status == "skipped_busy"
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_an_occurrence_whose_worker_died_is_taken_over_then_given_up(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    async def held_by_a_dead_worker(attempt):
        async with sm() as s:
            await worker.enqueue_due(s, MON_0800 + timedelta(seconds=1))
            occ = (await s.execute(
                select(ScheduleOccurrence).where(ScheduleOccurrence.schedule_id == sid)
            )).scalar_one()
            occ.status, occ.locked_by = "claimed", "node-dead"
            occ.locked_until, occ.attempt = MON_0800 + timedelta(minutes=5), attempt
            await s.commit()

    asyncio.run(held_by_a_dead_worker(1))
    _tick(sm, MON_0800 + timedelta(minutes=6))  # its lease ran out a minute ago
    [occ] = asyncio.run(_occurrences(sm, sid))
    assert (occ.status, occ.attempt, occ.locked_by) == ("succeeded", 2, "node-a")

    asyncio.run(_cleanup(sm))
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))
    asyncio.run(held_by_a_dead_worker(worker.MAX_ATTEMPTS))
    _tick(sm, MON_0800 + timedelta(minutes=6))
    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "stopped" in occ.error
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_a_workflow_that_does_not_allow_a_schedule_is_not_run(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0], triggers=None))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "cannot be scheduled" in occ.error
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_a_draft_version_is_never_run(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0], status="draft"))
    sid = asyncio.run(_schedule(sm, ws))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "published" in occ.error
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_a_schedule_never_reaches_another_workspaces_agent(sm) -> None:
    mine = asyncio.run(_workspace(sm, _ORGS[0]))
    theirs = asyncio.run(_workspace(sm, _ORGS[1]))
    sid = asyncio.run(_schedule(sm, mine, target=theirs["agent"]))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "no longer exists" in occ.error
    assert asyncio.run(_cases(sm, mine["tenant"])) == []
    assert asyncio.run(_cases(sm, theirs["tenant"])) == []


@db
def test_a_deleted_schedule_does_not_fire(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws, deleted_at=MON_0800 - timedelta(days=1)))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    assert asyncio.run(_occurrences(sm, sid)) == []


@db
def test_a_pause_after_the_claim_still_stops_the_run(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))
    now = MON_0800 + timedelta(seconds=5)

    async def claim_pause_execute():
        async with sm() as s:
            await worker.enqueue_due(s, now)
            await s.commit()
        _, occurrence_id = await worker._claim_one(sm, ws["tenant"], "node-a", now)
        async with sm() as s:
            await service.pause(s, await repo.get_schedule(s, sid, ws["tenant"], lock=True), by="user_bob")
            await s.commit()
        return await worker.execute(sm, occurrence_id, "node-a", now, _yes)

    assert asyncio.run(claim_pause_execute()) == "cancelled"
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_a_worker_whose_claim_was_taken_over_runs_nothing(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))
    now = MON_0800 + timedelta(seconds=5)
    later = now + worker.LEASE + timedelta(seconds=1)

    async def stale():
        async with sm() as s:
            await worker.enqueue_due(s, now)
            await s.commit()
        _, mine = await worker._claim_one(sm, ws["tenant"], "node-a", now)
        async with sm() as s:  # node-a stalls past its lease; node-b takes it over
            await repo.reclaim_expired_occurrences(s, later, worker.MAX_ATTEMPTS)
            await s.commit()
        _, theirs = await worker._claim_one(sm, ws["tenant"], "node-b", later)
        assert theirs == mine
        return await worker.execute(sm, mine, "node-a", later, _yes)

    assert asyncio.run(stale()) is None  # fenced out
    [occ] = asyncio.run(_occurrences(sm, sid))
    assert (occ.status, occ.locked_by, occ.attempt) == ("claimed", "node-b", 2)
    assert asyncio.run(_cases(sm, ws["tenant"])) == []


@db
def test_a_schedule_never_runs_another_workspaces_own_version(sm) -> None:
    mine = asyncio.run(_workspace(sm, _ORGS[0]))
    theirs = asyncio.run(_workspace(sm, _ORGS[1]))
    sid = asyncio.run(_schedule(sm, {**mine, "version": theirs["version"]}))

    _tick(sm, MON_0800 + timedelta(seconds=10))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "failed" and "not a published version" in occ.error
    assert asyncio.run(_cases(sm, mine["tenant"])) == []


@db
def test_every_schedule_due_at_the_same_time_is_run_in_that_tick(sm, monkeypatch) -> None:
    monkeypatch.setattr(worker, "BATCH", 2)
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    other = asyncio.run(_workspace(sm, _ORGS[1]))
    ids = [asyncio.run(_schedule(sm, ws)) for _ in range(4)] + [asyncio.run(_schedule(sm, other))]

    done = _tick(sm, MON_0800 + timedelta(seconds=10))

    assert done == {"enqueued": 5, "executed": 5, "imports": 0}
    statuses = [o.status for sid in ids for o in asyncio.run(_occurrences(sm, sid))]
    # A run finishes before the worker claims the next, so the agent is never found busy here.
    assert statuses == ["succeeded"] * 5


@db
def test_a_schedule_deleted_after_its_time_was_queued_does_not_run(sm) -> None:
    ws = asyncio.run(_workspace(sm, _ORGS[0]))
    sid = asyncio.run(_schedule(sm, ws))

    async def enqueue_then_delete():
        async with sm() as s:
            await worker.enqueue_due(s, MON_0800 + timedelta(seconds=1))
            schedule = await repo.get_schedule(s, sid, ws["tenant"], lock=True)
            schedule.deleted_at = MON_0800 + timedelta(seconds=2)
            await s.commit()

    asyncio.run(enqueue_then_delete())
    _tick(sm, MON_0800 + timedelta(seconds=30))

    [occ] = asyncio.run(_occurrences(sm, sid))
    assert occ.status == "cancelled"
    assert asyncio.run(_cases(sm, ws["tenant"])) == []
