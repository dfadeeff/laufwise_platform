"""Turning a remembered caller into one paragraph the agent may act on.

Two rules shape everything here.

**The appointment is never remembered, only re-read.** Memory holds a pointer; the start time the
agent says out loud comes from a live calendar read at call setup and is discarded with the call
(ADR-0011 D1). That keeps ADR-0002 #11 intact and makes a stale appointment impossible.

**The block is the enforcement.** Under the `greeting` policy the appointment never enters the
model's context at all, so there is nothing for it to disclose — the same mechanism as withholding
a tool, rather than a sentence in a prompt asking it not to. A prompt that says "do not mention
the appointment" while the appointment sits three lines above it is not a control.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.config import settings

# What a policy may put in front of the model. Ordered from least to most disclosed.
POLICIES = ("off", "greeting", "full")


def caller_key(tenant_id, phone_number: str) -> str | None:
    """A stable, tenant-scoped pseudonym for a phone number — or None if we must not remember.

    Without a configured pepper this returns None and recall is off everywhere: a hash of a phone
    number with no secret is a lookup table anyone with the table can reverse. Fail closed.
    """
    pepper = (settings.caller_memory_pepper or "").strip()
    number = _e164(phone_number)
    if not pepper or not number:
        return None
    return hashlib.sha256(f"{pepper}:{tenant_id}:{number}".encode()).hexdigest()


def _e164(phone_number: str | None) -> str:
    """Normalise so that the same phone is the same key. Anything unrecognisable is no key."""
    if not phone_number:
        return ""
    digits = re.sub(r"[^\d+]", "", phone_number)
    return digits if re.fullmatch(r"\+\d{6,15}", digits) else ""


def recall_block(
    *,
    policy: str,
    display_name: str | None,
    next_start: str | None = None,
) -> str | None:
    """The developer message describing a returning caller, or None when there is nothing to say.

    Written as instructions rather than as facts on purpose: the agent is told what the number
    means (a hint), what it does not mean (proof), and what to do when the person on the line is
    somebody else — which, for a shared family phone, is a normal Tuesday.
    """
    if policy not in ("greeting", "full") or not display_name:
        return None

    lines = [
        f"This call comes from a number a patient with the surname {display_name} has used before.",
        "You may greet them by that surname.",
        "The number is a hint, not proof of who is on the line. If they ask about an existing "
        "appointment, or want to change or cancel anything, you must still verify them with "
        "their name, date of birth and the appointment's own date and time.",
        "If they are not that person, drop this entirely and continue as you would with anyone.",
        "Never mention that you recognised the number, or that anything was remembered.",
    ]
    if policy == "full" and next_start:
        lines.insert(
            2,
            f"Their next appointment is at {next_start}. You may state it once when greeting "
            "them. Say nothing else about it, and nothing about any other appointment, until "
            "they are verified.",
        )
    return " ".join(lines)


def projection_is_recordable(projection: dict[str, Any] | None) -> bool:
    """Whether a call established enough to bind this number to this patient.

    The binding is the whole exposure under the `full` policy, so it is only ever minted by a call
    that actually checked a date of birth (ADR-0011 D5).
    """
    return bool(projection and projection.get("verified") and projection.get("patient_id"))
