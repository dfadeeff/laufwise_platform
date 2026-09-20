"""The caller memory store: one practice's agent remembering the people who ring it.

Implements the `MemoryStore` seam against Postgres. Everything it writes comes from
`BookingSession.memory_projection()`, which returns nothing at all unless the call actually
verified who it was speaking to — so the binding between a phone number and a patient can only
ever be created by a real date-of-birth check (ADR-0011 D5).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import repo


class CallerMemoryStore:
    """Scoped to one tenant and one agent at construction, so no call site can widen it."""

    def __init__(
        self,
        sessions: async_sessionmaker,
        *,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> None:
        self._sessions = sessions
        self._tenant_id = tenant_id
        self._agent_id = agent_id

    async def recall(self, subject_id: str) -> dict[str, Any] | None:
        async with self._sessions() as session:
            row = await repo.recall_caller(
                session,
                tenant_id=self._tenant_id,
                agent_id=self._agent_id,
                caller_hash=subject_id,
            )
            if row is None:
                return None
            return {
                "patient_id": row.patient_id,
                "display_name": row.display_name,
                "locale": row.locale,
                "last_outcome": row.last_outcome,
                "verified_before": row.verified_at is not None,
                "call_count": row.call_count,
            }

    async def remember(self, subject_id: str, summary: dict[str, Any]) -> None:
        async with self._sessions() as session:
            await repo.remember_caller(
                session,
                tenant_id=self._tenant_id,
                agent_id=self._agent_id,
                caller_hash=subject_id,
                projection=summary,
            )
