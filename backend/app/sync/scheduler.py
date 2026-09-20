"""The backend clock for the occupancy mirror (ADR-0010).

Run as a one-shot process, once per tier:

    python -m app.sync.scheduler near        # today … +7   — every 20 minutes
    python -m app.sync.scheduler horizon     # the whole booking horizon — nightly

It does the work itself rather than calling the HTTP API: the API's only credential is a Clerk
session token belonging to a signed-in human (app/auth/clerk.py, ADR-0003 D2), and inventing a
machine credential for a job that already shares the database would add an auth surface for
nothing. The governed path is untouched — this calls the SAME `execute_import_job` the Studio
button calls, so every day still goes through precondition → allowlist → execute → postcondition
→ trace. The clock decides *when* and *over which days*; it decides nothing about what may be
written.

Two tiers rather than one (D2): a single frequent sweep of the whole horizon would be ~6000
practice-calendar day-reads a day against a system that already times out under manual load,
while a single nightly sweep would leave a phone booking invisible online for up to 24 hours.

Nothing here is held in memory between runs. The process starts, works, and exits; if it is
killed, the next fire repairs whatever it missed. That is the whole durability story, and it is
why there is no timer inside the API process.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.logging import configure_logging
from app.db import repo
from app.sync.jobs import execute_import_job
from app.sync.mirror import MAX_DAYS

log = logging.getLogger("scheduler")

# The named schedule an instance is armed with (`agent_instance.schedule`). One name, because one
# process is scheduled; a second process gets its own name, not a cron expression per instance.
SCHEDULE = "mirror"

# How far each tier looks. `near` is what makes a phone booking visible online quickly; `horizon`
# is what guarantees the far edge is covered as it rolls forward, and repairs whatever `near`
# failed on. Sized from MAX_DAYS so the sweep is never silently truncated by the orchestrator.
TIERS: dict[str, int] = {"near": 8, "horizon": MAX_DAYS}

# A job still `running` with no progress for this long is an orphan from a restarted process.
# Comfortably longer than a full horizon sweep, far shorter than the interval at which staleness
# starts to matter.
STALE_AFTER_MIN = 30

_BERLIN = ZoneInfo("Europe/Berlin")


def window_for(tier: str, today: date | None = None) -> dict[str, str]:
    """The days this tier covers, as the orchestrator's window.

    Computed from the clock every time, never read from the instance's stored parameters: a stored
    window is a fact about the day somebody typed it, and the booking horizon is a fact about
    today. That difference is why the far edge of the horizon used to rot (ADR-0010 D4).
    """
    first = today or date.today()
    return {
        "from": first.isoformat(),
        "to": (first + timedelta(days=TIERS[tier] - 1)).isoformat(),
    }


async def run_tier(tier: str) -> int:
    """Fire every armed instance for this tier. Returns the number of runs started."""
    window = window_for(tier, date.today())
    engine = create_async_engine(settings.sqlalchemy_url)
    started = 0
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            # Before the concurrency guard, never after: one orphan left `running` by a restart
            # would otherwise block this instance's every future tick, silently and forever.
            reclaimed = await repo.reclaim_stale_import_jobs(
                session, older_than_minutes=STALE_AFTER_MIN
            )
            if reclaimed:
                log.warning("reclaimed %d stale job(s) as interrupted", reclaimed)

            instances = await repo.scheduled_instances(session, SCHEDULE)
            log.info(
                "tier=%s window=%s..%s armed_instances=%d",
                tier, window["from"], window["to"], len(instances),
            )

            for instance in instances:
                running = await repo.running_import_job_for_instance(
                    session, instance.id, instance.tenant_id
                )
                if running is not None:
                    # A slower tier is still working. Skipping is right: the next fire is minutes
                    # away, and two runs over the same days would read the practice calendar twice
                    # to reach the same answer.
                    log.info("instance=%s busy (job=%s) — skipped", instance.id, running.id)
                    continue

                job = await repo.create_import_job(
                    session,
                    tenant_id=instance.tenant_id,
                    instance_id=instance.id,
                    trigger="schedule",
                )
                await session.commit()
                log.info("instance=%s job=%s started", instance.id, job.id)
                # The same worker the button uses; it owns its own engine and never raises —
                # a failure is recorded on the job, not thrown at the clock.
                await execute_import_job(job.id, instance.id, instance.tenant_id, window)
                started += 1
    finally:
        await engine.dispose()
    return started


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    tier = args[0] if args else ""
    if tier not in TIERS:
        print(f"usage: python -m app.sync.scheduler {{{'|'.join(TIERS)}}}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    started = asyncio.run(run_tier(tier))
    log.info("tier=%s done, %d run(s) started", tier, started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
