"""Short-lived authorization for Studio voice WebSocket sessions."""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal

VoiceLanguage = Literal["de", "en", "ar"]

@dataclass(frozen=True)
class StudioSession:
    tenant_id: str
    language: VoiceLanguage
    expires_at: float
    # The already-open conversation this call writes its timeline to. Created before the token is
    # issued, so the socket never has to decide where a turn belongs.
    conversation_id: uuid.UUID


class StudioVoiceSessions:
    """Issues unguessable, expiring offer tokens; provider keys never reach the browser."""

    def __init__(self) -> None:
        self._sessions: dict[str, StudioSession] = {}

    def create(
        self,
        tenant_id: str,
        language: VoiceLanguage = "de",
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = StudioSession(
            tenant_id=tenant_id,
            language=language,
            expires_at=time.time() + 900,
            conversation_id=conversation_id or uuid.uuid4(),
        )
        return token

    def authorize(self, token: str) -> StudioSession:
        self._prune()
        session = self._sessions.get(token)
        if session is None:
            raise KeyError(token)
        return session

    def _prune(self) -> None:
        now = time.time()
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > now}


studio_voice_sessions = StudioVoiceSessions()
