"""A workspace's schedules: creating, changing, pausing, resuming, deleting, running now
(ADR-0040 D3, D6, D8) — no FastAPI.

Everything the worker would refuse when a schedule fires is refused here when it is saved: the
rule, the timezone, the agent and its version (through the target's own `resolve`), and an input
that sets a person's judgment. Every change bumps `revision` and records who made it; a change
must name the revision it was made on, so two people editing cannot overwrite each other.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import AgentSchedule, ScheduleOccurrence
from app.schedules import rules
from app.schedules.targets import NotRunnable, OnOldClock, Resolved, TargetNotFound, target_for

DEFAULT_TIMEZONE = "Europe/Berlin"
# Provisional (ADR-0040 D6): set against the first real workload before the feature is switched on.
MAX_ACTIVE_PER_WORKSPACE = 20
UPCOMING = 5


class StaleRevision(Exception):
    """The schedule changed since it was read; nothing was overwritten."""


class LimitReached(Exception):
    """The workspace already has as many active schedules as it may."""


class AlreadyQueued(Exception):
    """A run of this schedule was queued at this very moment already."""


class WorkerNotRunning(Exception):
    """No schedule worker has ticked lately; moving a clock onto it now would stop the work."""


# A worker ticks every 30 s; four missed ticks and it is not running.
WORKER_ALIVE_FOR = timedelta(minutes=2)
# The old clock's two tiers (ADR-0010 D2), as schedules on the 30-minute grid.
_LEGACY_TIERS = (
    ("Next 8 days, every 30 minutes", {"kind": "every", "minutes": 30}, {"days": 8}),
    ("Whole booking horizon, nightly", {"kind": "times", "at": ["03:00"]}, {"days": 70}),
)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _changed(schedule: AgentSchedule, by: str, now: datetime) -> None:
    schedule.revision += 1
    schedule.updated_by, schedule.updated_at = by, now


async def _room_for_one_more(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    # Two administrators enabling a schedule at once must not both fit into the last place.
    await repo.lock_workspace_schedules(session, tenant_id)
    if await repo.active_schedule_count(session, tenant_id) >= MAX_ACTIVE_PER_WORKSPACE:
        raise LimitReached(
            f"this workspace already has {MAX_ACTIVE_PER_WORKSPACE} active schedules; pause one first"
        )


async def _resolve(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, target_id: uuid.UUID,
    version_id: uuid.UUID | None, input: dict,
) -> Resolved:
    target = target_for(kind)
    if version_id is None:
        latest = await target.latest_version(session, tenant_id, target_id)
        if latest is None:
            raise NotRunnable("this agent's workflow has no published version to run")
        version_id = latest.id
    resolved = await target.resolve(session, tenant_id, target_id, version_id)
    target.check_input(resolved, input)
    return resolved


async def schedulability(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, target_id: uuid.UUID
) -> dict[str, Any]:
    """Whether the agent's newest published version can be scheduled, and with which input form:
    the same check `create` makes, so the Studio offers exactly what would be accepted."""
    try:
        resolved = await _resolve(session, tenant_id, kind, target_id, None, {})
    except TargetNotFound:
        raise
    except NotRunnable as exc:
        return {
            "schedulable": False, "reason": str(exc), "version": None, "input_schema": None,
            "old_clock": isinstance(exc, OnOldClock),
        }
    return {
        "schedulable": True,
        "reason": None,
        "old_clock": False,
        "version": {"id": str(resolved.version.id), "number": resolved.version.number},
        "input_schema": target_for(kind).input_schema(resolved),
    }


def preview(rule: Any, tz: str | None, count: int, now: datetime | None = None) -> list[datetime]:
    """The next `count` times a rule would run, before it is saved."""
    return rules.upcoming(rule, rules.validate_timezone(tz or DEFAULT_TIMEZONE), _now(now), count)


def snapshot(schedule: AgentSchedule) -> dict[str, Any]:
    """What an occurrence copies from its schedule, so a later edit cannot change what it ran."""
    return {
        "tenant_id": schedule.tenant_id,
        "schedule_id": schedule.id,
        "schedule_revision": schedule.revision,
        "target_kind": schedule.target_kind,
        "target_id": schedule.target_id,
        "target_version_id": schedule.target_version_id,
        "input": schedule.input,
    }


async def create(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    by: str,
    *,
    kind: str,
    target_id: uuid.UUID,
    version_id: uuid.UUID | None,
    name: str,
    rule: Any,
    tz: str | None,
    input: dict,
    enabled: bool,
    now: datetime | None = None,
) -> AgentSchedule:
    now = _now(now)
    rule, tz = rules.validate(rule), rules.validate_timezone(tz or DEFAULT_TIMEZONE)
    resolved = await _resolve(session, tenant_id, kind, target_id, version_id, input)
    if enabled:
        await _room_for_one_more(session, tenant_id)
    schedule = AgentSchedule(
        id=uuid.uuid4(), tenant_id=tenant_id, target_kind=kind, target_id=target_id,
        target_version_id=resolved.version.id, name=name, rule=rule, timezone=tz, input=input,
        enabled=enabled, next_run_at=rules.next_after(rule, tz, now) if enabled else None,
        revision=1, created_by=by, updated_by=by, created_at=now, updated_at=now,
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def update(
    session: AsyncSession,
    schedule: AgentSchedule,
    by: str,
    revision: int,
    changes: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Apply `changes` (name, rule, timezone, input, version_id). The caller holds it locked."""
    now = _now(now)
    if revision != schedule.revision:
        raise StaleRevision(
            f"the schedule changed since it was opened (revision {schedule.revision}, not "
            f"{revision}); reload it"
        )
    rule = rules.validate(changes["rule"]) if "rule" in changes else schedule.rule
    tz = rules.validate_timezone(changes["timezone"]) if "timezone" in changes else schedule.timezone
    input = changes.get("input", schedule.input)
    version_id = changes.get("version_id", schedule.target_version_id)
    resolved = await _resolve(
        session, schedule.tenant_id, schedule.target_kind, schedule.target_id, version_id, input
    )
    # Only a different rule or timezone moves the next time; the Studio sends the rule with every
    # save. Recomputing it on a rename would skip a time that is due but not yet queued, without
    # a record that it was skipped.
    retimed = rule != schedule.rule or tz != schedule.timezone
    if "name" in changes:
        schedule.name = changes["name"]
    schedule.rule, schedule.timezone, schedule.input = rule, tz, input
    schedule.target_version_id = resolved.version.id
    if schedule.enabled and retimed:
        schedule.next_run_at = rules.next_after(rule, tz, now)
    _changed(schedule, by, now)


async def pause(
    session: AsyncSession, schedule: AgentSchedule, by: str, now: datetime | None = None
) -> None:
    """Stop it. A queued occurrence is cancelled; one already running finishes and is shown.
    The caller holds the schedule locked (`repo.get_schedule(..., lock=True)`)."""
    await _stop(session, schedule, by, _now(now), "paused before it started")


async def _stop(session: AsyncSession, schedule: AgentSchedule, by: str, now: datetime, why: str) -> None:
    schedule.enabled, schedule.next_run_at = False, None
    _changed(schedule, by, now)
    await repo.cancel_queued_occurrences(session, schedule.id, now, why)


async def resume(
    session: AsyncSession, schedule: AgentSchedule, by: str, now: datetime | None = None
) -> None:
    """Run it again from its next time; the times missed while it was paused are not run."""
    if schedule.enabled:
        return  # already on: its next time stays, due or not
    now = _now(now)
    # What could not run is refused now, not at its next time (e.g. back on the old clock).
    await _resolve(
        session, schedule.tenant_id, schedule.target_kind, schedule.target_id,
        schedule.target_version_id, schedule.input,
    )
    await _room_for_one_more(session, schedule.tenant_id)
    schedule.enabled = True
    schedule.next_run_at = rules.next_after(schedule.rule, schedule.timezone, now)
    _changed(schedule, by, now)


async def delete(
    session: AsyncSession, schedule: AgentSchedule, by: str, now: datetime | None = None
) -> None:
    """Marked deleted, never removed: its occurrences are the history of what it did."""
    now = _now(now)
    await _stop(session, schedule, by, now, "the schedule was deleted before it started")
    schedule.deleted_at = now


async def run_now(
    session: AsyncSession, schedule: AgentSchedule, now: datetime | None = None
) -> ScheduleOccurrence:
    """Queue one run now, whether the schedule is on or paused; the worker takes it on its next
    tick and treats it like any other (the same checks, the same overlap guard). Refused at once
    if the agent could not run."""
    await _resolve(
        session, schedule.tenant_id, schedule.target_kind, schedule.target_id,
        schedule.target_version_id, schedule.input,
    )
    occurrence_id = await repo.add_occurrence(
        session, **snapshot(schedule), scheduled_for=_now(now), origin="run_now", status="queued"
    )
    if occurrence_id is None:
        raise AlreadyQueued("a run of this schedule was queued at this very moment already")
    return await session.get(ScheduleOccurrence, occurrence_id)


def occurrence_view(o: ScheduleOccurrence) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "scheduled_for": o.scheduled_for,
        "origin": o.origin,
        "status": o.status,
        "outcome": o.outcome,
        "run_ref": o.run_ref,
        "error": o.error,
        "attempt": o.attempt,
        "schedule_revision": o.schedule_revision,
        "finished_at": o.finished_at,
    }


async def view(session: AsyncSession, schedule: AgentSchedule, now: datetime | None = None) -> dict[str, Any]:
    """What the Studio shows: the schedule, what it runs, its next times and its last run."""
    now = _now(now)
    target = target_for(schedule.target_kind)
    number = (
        await repo.definition_version_number(session, schedule.target_version_id)
        if schedule.target_version_id else None
    )
    try:
        latest = await target.latest_version(session, schedule.tenant_id, schedule.target_id)
        if latest is not None and latest.id != schedule.target_version_id:
            # Offer only a version the schedule could be moved to.
            await target.resolve(session, schedule.tenant_id, schedule.target_id, latest.id)
    except NotRunnable:
        latest = None
    last = await repo.schedule_occurrences(session, schedule.id, schedule.tenant_id, 1)
    try:  # the input form of the version it runs, which its input was saved against
        pinned = await target.resolve(session, schedule.tenant_id, schedule.target_id, schedule.target_version_id)
        input_schema = target.input_schema(pinned)
    except NotRunnable:
        input_schema = None
    return {
        "id": str(schedule.id),
        "name": schedule.name,
        "target": {
            "kind": schedule.target_kind,
            "id": str(schedule.target_id),
            "name": await target.name(session, schedule.tenant_id, schedule.target_id),
            "key": await target.key(session, schedule.tenant_id, schedule.target_id),
        },
        "version": {"id": str(schedule.target_version_id), "number": number}
        if schedule.target_version_id else None,
        "newer_version": {"id": str(latest.id), "number": latest.number}
        if latest and number is not None and latest.number > number else None,
        "rule": schedule.rule,
        "timezone": schedule.timezone,
        "input": schedule.input,
        "input_schema": input_schema,
        "enabled": schedule.enabled,
        "next_run_at": schedule.next_run_at,
        "upcoming": rules.upcoming(schedule.rule, schedule.timezone, now, UPCOMING),
        "revision": schedule.revision,
        "created_by": schedule.created_by,
        "updated_by": schedule.updated_by,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "last": occurrence_view(last[0]) if last else None,
    }


async def worker_status(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """Whether the platform's schedule worker is running: without it, schedules are kept and
    nothing fires them. The Studio says so rather than letting a schedule look armed."""
    last = await repo.last_worker_tick(session)
    return {"running": last is not None and last >= _now(now) - WORKER_ALIVE_FOR, "last_tick_at": last}


async def move_off_old_clock(
    session: AsyncSession, tenant_id: uuid.UUID, instance_id: uuid.UUID, by: str, now: datetime | None = None
) -> list[AgentSchedule]:
    """Move this workspace's runbook from the old clock (`agent_instance.schedule`, two Railway
    cron services) onto two schedules with the same intent, in one transaction, so it is on
    exactly one clock throughout (ADR-0040 D9). Whichever of its deployments the old clock fires
    is disarmed; the schedules run `instance_id`, the deployment on screen. Refused while no
    worker runs: that would stop it."""
    now = _now(now)
    instance = await repo.get_instance(session, instance_id, tenant_id)
    if instance is None:
        raise TargetNotFound("no such deployment")
    template = await repo.get_template_by_id(session, instance.template_id)
    armed = await repo.armed_on_old_clock(session, tenant_id, getattr(template, "name", ""))
    if not armed:
        raise NotRunnable("this is not on the old clock")
    if not (await worker_status(session, now))["running"]:
        raise WorkerNotRunning(
            "the platform's schedule worker is not running yet; moving now would stop this "
            "deployment's sync until it does"
        )
    for old in armed:
        old.schedule = None
    return [
        await create(
            session, tenant_id, by, kind="runbook", target_id=instance.id, version_id=instance.id,
            name=name, rule=rule, tz=None, input=input, enabled=True, now=now,
        )
        for name, rule, input in _LEGACY_TIERS
    ]
