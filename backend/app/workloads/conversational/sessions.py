"""Short-lived authorization for voice WebSocket sessions (Studio and telephony).

A media socket is reachable from the internet, so it must not accept a caller that merely knows
the URL. The surface that starts a call mints an unguessable, expiring token first; the socket
trades it for the conversation the audio belongs to. Provider keys never reach the client."""

from __future__ import annotations

import secrets
import time
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
    expires_at: float
    # The already-open conversation this call writes its timeline to. Created before the token is
    # issued, so the socket never has to decide where a turn belongs.
    conversation_id: uuid.UUID
    # The number the call came from, for the summary email (spec §3.9). Technical call
    # information only — never written to the patient record, never an identity check.
    caller_number: str | None = None
    # The calendar this call books into, resolved from the instance's bound connection BEFORE the
    # token is minted — so a misconfigured practice fails as a readable HTTP error rather than as
    # a call that connects and then cannot book. None means the in-memory sandbox.
    calendar: Any | None = None


class VoiceSessions:
    """Issues unguessable, expiring offer tokens; provider keys never reach the browser."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    def create(
        self,
        tenant_id: str,
        language: VoiceLanguage = "de",
        *,
        conversation_id: uuid.UUID | None = None,
        caller_number: str | None = None,
        calendar: Any | None = None,
    ) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = VoiceSession(
            tenant_id=tenant_id,
            language=language,
            expires_at=time.time() + 900,
            conversation_id=conversation_id or uuid.uuid4(),
            caller_number=caller_number,
            calendar=calendar,
        )
        return token

    def authorize(self, token: str) -> VoiceSession:
        self._prune()
        session = self._sessions.get(token)
        if session is None:
            raise KeyError(token)
        return session

    def _prune(self) -> None:
        now = time.time()
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > now}


voice_sessions = VoiceSessions()
