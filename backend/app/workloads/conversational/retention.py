"""Automatic deletion of stored transcripts (spec §4.1, §7).

The practice tells callers their conversation is kept for a fixed number of days and then
deleted. That sentence is a commitment, so it is kept by a loop the application runs itself rather
than by a cron job someone has to remember to install: a retention promise that depends on an
operator is a retention promise that quietly lapses.

Two things it deliberately does NOT do. It does not touch the audio, because there is none — the
pipeline never writes any (spec §4.1), which is a property of what was built rather than a policy
that has to be enforced. And it does not delete the conversation row: afterwards you can still see
that a call happened, when, on which agent and how it ended, you just cannot read what was said.
That is the line between honouring a retention period and destroying the audit trail.

The retention period comes from the practice knowledge base, not from an environment variable —
it is a statement the practice makes to its patients, and it belongs with the rest of them.
"""

from __future__ import annotations

import asyncio
import logging

from app.db import repo
from app.db.session import get_sessionmaker
from app.workloads.conversational.practice import load_practice

log = logging.getLogger(__name__)

# Once a day is the right cadence for a policy measured in days: a transcript that survives a few
# hours past its 30th day has not broken the promise, and a tighter loop would only add load.
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60


async def purge_once() -> int:
    """One sweep. Returns how many conversations were cleared."""
    days = load_practice().policy.transcript_retention_days
    async with get_sessionmaker()() as session:
        cleared = await repo.purge_expired_transcripts(session, older_than_days=days)
    if cleared:
        log.info("purged transcripts older than %s days from %s conversations", days, cleared)
    return cleared


async def run_retention_sweeps() -> None:
    """Sweep now, then once a day, forever. Cancelled with the application.

    A failed sweep is logged and the loop continues. Retention is a daily obligation, not a
    one-shot one: a database blip today must not stop tomorrow's sweep, and stopping the loop on
    the first error is exactly how a retention promise silently lapses.
    """
    while True:
        try:
            await purge_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — see docstring: one bad sweep must not end the loop
            log.exception("transcript retention sweep failed; will retry")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
