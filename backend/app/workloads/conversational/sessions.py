"""Short-lived authorization for voice WebSocket sessions (Studio and telephony).

A media socket is reachable from the internet, so it must not accept a caller that merely knows
the URL. The surface that starts a call mints an unguessable, expiring token first; the socket
trades it for the conversation the audio belongs to. Provider keys never reach the client."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal

VoiceLanguage = Literal["de", "en", "ar"]

@dataclass(frozen=True)
class VoiceSession:
    tenant_id: str
    language: VoiceLanguage
    expires_at: float
    # The already-open conversation this call writes its timeline to. Created before the token is
    # issued, so the socket never has to decide where a turn belongs.
    conversation_id: uuid.UUID


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
    ) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = VoiceSession(
            tenant_id=tenant_id,
            language=language,
            expires_at=time.time() + 900,
            conversation_id=conversation_id or uuid.uuid4(),
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
