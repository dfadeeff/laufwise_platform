"""Recall, assembled in the webhook — before the caller hears a ring, and never in their way.

This runs in the Twilio HTTP request beside the calendar resolution, for the reason that module
already states about calendars: what can fail should fail here, as a readable error, rather than
inside a socket that has already started ringing. It also means the pipeline keeps knowing nothing
about the database.

Every failure mode collapses to the same answer — no recall, call proceeds as it always did:
nothing remembered, a slow database, a calendar that will not answer, a missing pepper. Memory is
an accelerator, and an accelerator that can stop a call is a liability (ADR-0011 D6).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.memory.caller import CallerMemoryStore
from app.memory.recall import caller_key, recall_block

log = logging.getLogger(__name__)

# The whole budget for remembering someone, database and calendar together. A caller waits for
# TwiML while this runs, so it is short on purpose.
RECALL_TIMEOUT_SECONDS = 2.5


async def compose_recall(
    sessions,
    *,
    tenant_id,
    agent_id,
    config,
    caller_number: str | None,
    calendar: Any | None,
) -> tuple[str | None, str | None]:
    """Return `(recall_block, caller_hash)` for this call.

    The hash comes back even when there is nothing to recall: a first-time caller who verifies
    themselves during the call is exactly who we want to recognise next time.
    """
    policy = getattr(config, "recall_policy", "off")
    key = caller_key(tenant_id, caller_number or "")
    if policy == "off" or key is None or agent_id is None:
        return None, key

    try:
        async with asyncio.timeout(RECALL_TIMEOUT_SECONDS):
            store = CallerMemoryStore(sessions, tenant_id=tenant_id, agent_id=agent_id)
            known = await store.recall(key)
            if not known or not known.get("display_name"):
                return None, key
            next_start = None
            if policy == "full":
                next_start = await _next_appointment(calendar, known.get("patient_id"))
            return (
                recall_block(
                    policy=policy,
                    display_name=known.get("display_name"),
                    next_start=next_start,
                ),
                key,
            )
    except Exception:  # noqa: BLE001 — including the timeout: no failure here stops a call
        log.exception("recall failed for agent %s; continuing without it", agent_id)
        return None, key


async def _next_appointment(calendar: Any | None, patient_id: int | None) -> str | None:
    """The one fact the `full` policy may speak, read live and never stored (ADR-0011 D1).

    Read here rather than remembered, which is both what ADR-0002 #11 requires and the reason a
    recalled appointment cannot be out of date: there is no remembered copy to go stale.
    """
    if calendar is None or patient_id is None:
        return None
    appointments = await asyncio.to_thread(
        calendar.appointments_for, patient_id, upcoming_only=True
    )
    if not appointments:
        return None
    return getattr(appointments[0], "start", None)
