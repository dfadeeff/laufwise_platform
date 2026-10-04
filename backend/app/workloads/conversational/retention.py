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

The retention period is the practice's, not an environment variable's — it is a statement the
practice makes to its patients. A Studio agent carries its own (`transcript_retention_days`); a
legacy instance with no Studio configuration keeps the knowledge base's, because that file IS its
practice. Each agent's transcripts are swept by that agent's number, never by another tenant's.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from app.agents.config import AgentConfig
from app.agents.service import instance_config
from app.db import repo
from app.db.session import get_sessionmaker
from app.workloads.conversational.practice import load_practice

log = logging.getLogger(__name__)

# Once a day is the right cadence for a policy measured in days: a transcript that survives a few
# hours past its 30th day has not broken the promise, and a tighter loop would only add load.
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60

# How long a practice's agent may remember someone who rang it (ADR-0011 D6). A constant rather
# than a per-agent field: the control that matters is whether recall is on at all, and that one
# already exists in the contract. Six months is long enough that a patient with a twice-yearly
# check-up is still recognised, and short enough to be a sentence in a privacy notice.
CALLER_MEMORY_DAYS = 180


def retention_days(instance) -> int:
    """How long this agent promised its callers their transcript is kept."""
    if not instance.runtime_config:
        return load_practice().policy.transcript_retention_days
    try:
        return instance_config(instance).transcript_retention_days
    except Exception:  # noqa: BLE001 — one unreadable agent must not stop everyone's sweep
        log.exception("instance %s has an unreadable configuration; using the default", instance.id)
        return AgentConfig.model_fields["transcript_retention_days"].default


async def purge_once(*, sessionmaker=None) -> int:
    """One sweep of both promises. Returns how many conversations were cleared."""
    async with (sessionmaker or get_sessionmaker())() as session:
        periods: dict[int, list] = defaultdict(list)
        for instance in await repo.instances_with_transcripts(session):
            periods[retention_days(instance)].append(instance.id)
        cleared = 0
        for days, instance_ids in periods.items():
            swept = await repo.purge_expired_transcripts(
                session, older_than_days=days, instance_ids=instance_ids
            )
            if swept:
                log.info("purged transcripts older than %s days from %s conversations", days, swept)
            cleared += swept
        forgotten = await repo.purge_expired_caller_memory(
            session, older_than_days=CALLER_MEMORY_DAYS
        )
    if forgotten:
        log.info("forgot %s callers last heard from over %s days ago", forgotten, CALLER_MEMORY_DAYS)
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
            log.exception("retention sweep failed; will retry")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
