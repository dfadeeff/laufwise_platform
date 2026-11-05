"""The platform's one clock for workspace schedules (ADR-0040 D2, D5).

    python -m app.schedules.worker

The host keeps this one command running: the same command for every workspace and every
schedule, which never changes when a workspace adds one. Any number may run; Postgres decides
who does what.

Each tick:
1. **Reclaim** occurrences whose worker died (lease ran out): queue them again, or give up
   after MAX_ATTEMPTS.
2. **Enqueue** due schedules (locked, so one worker takes each): one occurrence per due time,
   unique by the schema, and the schedule moves to its next time in the same commit. Times
   missed while nothing ran collapse into one run, and the miss is recorded.
3. **Claim** one queued occurrence per workspace in turn, so no workspace waits behind another's
   backlog. An agent already running on a schedule is skipped, not run twice.
4. **Execute** each claim: re-check that the schedule is still on and that its creator is still
   an administrator, then start the target's ordinary governed run. The occurrence's row stays
   locked while it runs, which fences out any other worker; the run and the occurrence's result
   are committed together, or the run is rolled back and the failure recorded.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import repo
from app.db.models import ScheduleOccurrence
from app.schedules import rules, service
from app.schedules.authority import still_admin
from app.schedules.targets import TARGETS, Busy, NotRunnable, target_for

log = logging.getLogger("schedules")

TICK_SECONDS = 30
LEASE = timedelta(minutes=5)  # a case computes in seconds; its functions stop at 10 s each
MAX_ATTEMPTS = 3  # a worker dying under the same occurrence three times is not bad luck
BATCH = 50  # schedules enqueued per transaction
MAX_MISSED = 5000  # a year of the 30-minute grid is ~17 500; this bounds one catch-up

Authority = Callable[[str | None, str], Awaitable[bool | None]]


def _local(moment: datetime, tz: str) -> str:
    return moment.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


async def enqueue_due(session: AsyncSession, now: datetime) -> int:
    """Turn up to BATCH due schedules into their occurrences. The caller commits; `tick` calls
    it until fewer than BATCH come back, so every schedule due at 08:00 is queued at 08:00."""
    count = 0
    for schedule in await repo.due_schedules(session, now, BATCH):
        due, missed = schedule.next_run_at, []
        following = rules.next_after(schedule.rule, schedule.timezone, due)
        while following <= now and len(missed) < MAX_MISSED:
            missed.append(due)
            due, following = following, rules.next_after(schedule.rule, schedule.timezone, following)
        if missed:
            tz = schedule.timezone
            await repo.add_occurrence(
                session, **service.snapshot(schedule), scheduled_for=missed[0], status="skipped_missed",
                finished_at=now,
                error=(
                    f"{len(missed)} run(s) missed while nothing ran, from {_local(missed[0], tz)} "
                    f"to {_local(missed[-1], tz)}; the one due {_local(due, tz)} ran instead"
                ),
            )
        await repo.add_occurrence(session, **service.snapshot(schedule), scheduled_for=due, status="queued")
        schedule.next_run_at = following
        count += 1
    return count


async def _claim_one(
    sm: async_sessionmaker, tenant_id: uuid.UUID, node: str, now: datetime
) -> tuple[bool, uuid.UUID | None]:
    """Claim this workspace's longest-waiting occurrence, in its own transaction, so a worker
    holds at most one agent's lock at a time. Returns whether an occurrence was taken off the
    queue (claimed, or skipped as busy), and the id of a claimed one."""
    async with sm() as session:
        occurrence = await repo.next_queued_occurrence(session, tenant_id)
        if occurrence is None:
            return False, None  # nothing left, or held by another worker
        await repo.lock_target(session, occurrence.target_id)
        if await repo.target_is_running(session, occurrence.target_id, now):
            occurrence.status, occurrence.finished_at = "skipped_busy", now
            occurrence.error = "the agent was still running on a schedule; this time was skipped"
            await session.commit()
            return True, None
        occurrence.status, occurrence.locked_by = "claimed", node
        occurrence.locked_until, occurrence.attempt = now + LEASE, occurrence.attempt + 1
        await session.commit()
        return True, occurrence.id


def _finish(occurrence: ScheduleOccurrence, status: str, now: datetime, error: str = "") -> None:
    # `locked_by` stays: which worker ran it is worth knowing afterwards.
    occurrence.status, occurrence.finished_at, occurrence.error = status, now, error
    occurrence.locked_until = None


async def execute(
    sm: async_sessionmaker, occurrence_id: uuid.UUID, node: str, now: datetime, authority: Authority
) -> str | None:
    """Run one claimed occurrence. Returns its final status, or None if this worker no longer
    holds it."""
    async with sm() as session:
        occurrence = await repo.claimed_occurrence(session, occurrence_id, node)
        if occurrence is None:
            return None
        # Ask about the creator before locking the schedule, so a slow answer never holds up
        # someone pausing it.
        creator = await repo.schedule_creator(session, occurrence.schedule_id)
        tenant = await repo.get_tenant(session, occurrence.tenant_id)
        allowed = await authority(tenant.clerk_org_id if tenant else None, creator or "")
        # The boundary of D5: from here the schedule is locked until the run is committed, so a
        # pause either lands before this (and cancels it) or after the run.
        schedule = await repo.get_schedule(session, occurrence.schedule_id, occurrence.tenant_id, lock=True)
        if schedule is None or (occurrence.origin == "schedule" and not schedule.enabled):
            _finish(occurrence, "cancelled", now, "paused or deleted before it started")
            await session.commit()
            return occurrence.status

        if allowed is None:
            _finish(
                occurrence, "failed", now,
                "could not confirm that the person who set up this schedule is still an "
                "administrator of the workspace; it runs again at its next time",
            )
        elif allowed is False:
            _finish(
                occurrence, "failed", now,
                "the person who set up this schedule is no longer an administrator of the "
                "workspace; the schedule was paused until an administrator resumes it",
            )
            await service.pause(session, schedule, by="platform", now=now)
        else:
            title = f"{schedule.name} · {_local(occurrence.scheduled_for, schedule.timezone)}"
            try:
                async with session.begin_nested():
                    started = await target_for(occurrence.target_kind).start(session, occurrence, title)
            except Busy as exc:
                _finish(occurrence, "skipped_busy", now, str(exc))
            except NotRunnable as exc:
                _finish(occurrence, "failed", now, str(exc))
            except Exception as exc:  # a run's failure is recorded on it, never thrown at the clock
                log.exception("occurrence %s failed", occurrence.id)
                _finish(occurrence, "failed", now, f"the run failed: {type(exc).__name__}: {exc}")
            else:
                occurrence.run_ref, occurrence.outcome = started.run_ref, started.outcome
                # Work that goes on after this commit is "started"; its run carries the outcome.
                _finish(occurrence, "succeeded" if started.finished else "started", now)
        await session.commit()
        if occurrence.status == "started" and started.after_commit is not None:
            started.after_commit()
        log.info(
            "occurrence=%s tenant=%s schedule=%s status=%s run=%s",
            occurrence.id, occurrence.tenant_id, occurrence.schedule_id, occurrence.status,
            occurrence.run_ref,
        )
        return occurrence.status


async def tick(
    sm: async_sessionmaker,
    node: str,
    now: datetime | None = None,
    authority: Authority = still_admin,
) -> dict[str, int]:
    """One pass. `now` is fixed for the pass when given (tests); otherwise the real clock."""
    def clock() -> datetime:
        return now or datetime.now(timezone.utc)

    async with sm() as session:
        await repo.worker_ticked(session, node, clock())
        await repo.reclaim_expired_occurrences(session, clock(), MAX_ATTEMPTS)
        for target in TARGETS.values():
            await target.recover(session, clock())
        await session.commit()
    enqueued = 0
    while True:
        async with sm() as session:
            batch = await enqueue_due(session, clock())
            await session.commit()
        enqueued += batch
        if batch < BATCH:
            break

    executed = 0
    while True:  # one occurrence per workspace per round, until the queue is empty
        async with sm() as session:
            tenants = await repo.tenants_with_queued_occurrences(session)
        progressed = False
        for tenant_id in tenants:
            taken, occurrence_id = await _claim_one(sm, tenant_id, node, clock())
            progressed |= taken
            if occurrence_id is None:
                continue
            # Run it right away: its lease starts at the claim, and must not run out while it
            # waits behind other workspaces' runs.
            try:
                await execute(sm, occurrence_id, node, clock(), authority)
            except Exception:  # its lease runs out and another tick takes it over
                log.exception("occurrence %s could not be executed", occurrence_id)
            executed += 1
        if not progressed:
            break  # empty, or what is queued is held by other workers
    return {"enqueued": enqueued, "executed": executed}


def node_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


async def run_forever(sm: async_sessionmaker, node: str) -> None:
    log.info("schedule worker %s started, every %ds", node, TICK_SECONDS)
    while True:
        try:
            done = await tick(sm, node)
            if done["enqueued"] or done["executed"]:
                log.info("tick: %s", done)
        except Exception:  # one bad tick must not stop every workspace's schedules
            log.exception("schedule tick failed")
        await asyncio.sleep(TICK_SECONDS)


def main() -> int:
    from app.config import settings
    from app.core.logging import configure_logging
    from app.db.session import get_sessionmaker

    configure_logging(settings.log_level)
    asyncio.run(run_forever(get_sessionmaker(), node_name()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
