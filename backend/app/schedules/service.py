"""Changing a schedule's state (ADR-0040) — no FastAPI. Pausing is always safe and cancels what
has not started; resuming runs from the next time, never the ones missed while paused."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import AgentSchedule
from app.schedules import rules


def _changed(schedule: AgentSchedule, by: str, now: datetime) -> None:
    schedule.revision += 1
    schedule.updated_by, schedule.updated_at = by, now


async def pause(
    session: AsyncSession, schedule: AgentSchedule, by: str, now: datetime | None = None
) -> None:
    """Stop it. A queued occurrence is cancelled; one already running finishes and is shown.
    The caller holds the schedule locked (`repo.get_schedule(..., lock=True)`)."""
    now = now or datetime.now(timezone.utc)
    schedule.enabled, schedule.next_run_at = False, None
    _changed(schedule, by, now)
    await repo.cancel_queued_occurrences(session, schedule.id, now)


def resume(schedule: AgentSchedule, by: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    schedule.enabled = True
    schedule.next_run_at = rules.next_after(schedule.rule, schedule.timezone, now)
    _changed(schedule, by, now)
