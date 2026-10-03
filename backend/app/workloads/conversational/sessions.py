"""Short-lived authorization for voice WebSocket sessions (Studio and telephony).

A media socket is reachable from the internet, so it must not accept a caller that merely knows
the URL. The surface that starts a call mints an unguessable, expiring token first; the socket
trades it for the conversation the audio belongs to. Provider keys never reach the client.

The admission is stored (`voice_call_token`), not held in this process: the webhook and the socket
are two requests, and with more than one replica, or across a redeploy, they reach different
processes. `app.agents.runtime` issues and redeems it."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Literal

# The languages the agent detects and answers in. German, Russian and English are the practice
# specification's three (spec §1); Arabic was already built and tested, so it stays — a language
# nobody has to remove is a language nobody has to argue about.
VoiceLanguage = Literal["de", "en", "ru", "ar"]

@dataclass(frozen=True)
class VoiceSession:
    tenant_id: str
    language: VoiceLanguage
    # The already-open conversation this call writes its timeline to. Created before the token is
    # issued, so the socket never has to decide where a turn belongs.
    conversation_id: uuid.UUID
    # The number the call came from, for the summary email (spec §3.9). Technical call
    # information only — never written to the patient record, never an identity check.
    caller_number: str | None = None
    # The calendar this call books into, resolved from the instance's bound connection. The webhook
    # resolves it once BEFORE the token is minted — so a misconfigured practice fails as a readable
    # HTTP error rather than as a call that connects and then cannot book — and the socket resolves
    # it again from the same immutable instance. None means the in-memory sandbox.
    calendar: Any | None = None
    config: Any | None = None
    contracts: Any | None = None
    rehearsal: bool = True
    base_prompt: str | None = None
    # One paragraph about a returning caller, composed in the webhook where the database and the
    # calendar are both reachable (ADR-0011 D6). None is the normal case and means the call runs
    # exactly as it did before memory existed.
    recall: str | None = None
    # The pseudonym this call writes its result back under, when it verifies anyone.
    caller_hash: str | None = None
    agent_id: uuid.UUID | None = None
    # The documents the snapshot pinned (ADR-0017), read in full into the prompt.
    knowledge: list[dict] | None = None


# How long an admitted call may take to open its audio. Twilio connects within seconds; a Studio
# tester may sit on the page a while before speaking.
TOKEN_TTL_SECONDS = 900


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """What is stored. A leaked table row cannot be replayed as a call."""
    return hashlib.sha256(token.encode()).hexdigest()
