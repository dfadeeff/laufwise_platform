"""Connection DTOs (ADR-0003). Credentials come IN on create and are immediately encrypted;
they never appear in any response — a summary carries only non-secret metadata."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConnectionCreate(BaseModel):
    type: str = "calendar"
    adapter: str = "thevea"
    # The user's own credentials (e.g. {"username": ..., "password": ...}). Encrypted at rest;
    # never persisted or returned in plaintext.
    credentials: dict[str, str]
    config: dict[str, str] = Field(default_factory=dict)


class ConnectionSummary(BaseModel):
    id: str
    type: str
    adapter: str
    created_at: datetime

    @classmethod
    def of(cls, conn) -> "ConnectionSummary":
        return cls(
            id=conn.id.hex if isinstance(conn.id, UUID) else str(conn.id),
            type=conn.type,
            adapter=conn.adapter,
            created_at=conn.created_at,
        )