"""Short-lived authorization for Studio voice WebSocket sessions."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

@dataclass(frozen=True)
class StudioSession:
    tenant_id: str
    expires_at: float


class StudioVoiceSessions:
    """Issues unguessable, expiring offer tokens; provider keys never reach the browser."""

    def __init__(self) -> None:
        self._sessions: dict[str, StudioSession] = {}

    def create(self, tenant_id: str) -> str:
        self._prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = StudioSession(tenant_id=tenant_id, expires_at=time.time() + 900)
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
