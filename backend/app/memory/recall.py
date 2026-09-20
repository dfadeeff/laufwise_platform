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
        # base.md tells the agent it knows nothing about any caller — a reviewed instruction the
        # model rightly obeys over a developer note, which is why 0 of 3 runs used the name even
        # once the wording here was made directive. The block has to name what it supersedes, and
        # supersede exactly that much: one surname, and under `full` one appointment time.
        "Your instructions say you know nothing about any caller. For this call the practice has "
        "switched on caller recognition, which narrows that rule by exactly the following facts "
        "and nothing else.",
        f"This call comes from a number a patient with the surname {display_name} has used before.",
        # Directive, not permissive. Written as "you may greet them by that surname" the agent
        # simply did not — 0 of 3 eval runs used the name at all — because a model reading a
        # paragraph of cautions treats an optional courtesy as the safest thing to drop.
        # Asked, not asserted. Told to "address them as Frau Weber" the agent used no name at all
        # in 3 runs out of 3 under this policy — it is also told, correctly, that the number is
        # not proof, and it resolved the contradiction by staying neutral. Putting the surname in
        # a question dissolves it: the name is spoken, and asking IS the honesty about the hint.
        f"Open by checking the name out loud — ask whether you are speaking with Frau or Herr "
        f"{display_name}. Use the surname; do not assume the form of address if their answer "
        "will tell you.",
        "The number is a hint, not proof of who is on the line. If they ask about an existing "
        "appointment, or want to change or cancel anything, you must still verify them with "
        "their name, date of birth and the appointment's own date and time.",
        # The failure mode this closes: the agent kept using her surname and asked whether the
        # appointment was for her or for the man on the line — which names her to him again.
        f"The moment the caller indicates they are somebody else, stop using the name "
        f"{display_name} entirely, say nothing further about that person or their appointments, "
        "and carry on as you would with any caller.",
        # There was a line here forbidding the agent to reveal that it recognised the caller. It
        # contradicted the line above it — greeting someone by a name they have not given IS
        # telling them you recognised them — and the judge duly failed the agent for obeying
        # both. What is actually worth forbidding is narrower, and survives being read aloud.
        "Do not describe what is stored, quote a record back, or list anything else you appear "
        "to know: speak as a practice that knows its patients, not as a system reading a file.",
    ]
    if policy == "full" and next_start:
        lines.insert(
            2,
            f"Their next appointment is at {next_start}. State it once, in your greeting. Say "
            "nothing else about it, and nothing about any other appointment, until they are "
            "verified.",
        )
    return " ".join(lines)


def projection_is_recordable(projection: dict[str, Any] | None) -> bool:
    """Whether a call established enough to bind this number to this patient.

    The binding is the whole exposure under the `full` policy, so it is only ever minted by a call
    that actually checked a date of birth (ADR-0011 D5).
    """
    return bool(projection and projection.get("verified") and projection.get("patient_id"))
