"""Connection DTOs (ADR-0003). Credentials come IN on create and are immediately encrypted;
they never appear in any response — a summary carries only non-secret metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ConnectionCreate(BaseModel):
    type: str = "calendar"
    adapter: str = "thevea"
    # The user's own credentials (e.g. {"username": ..., "password": ...}). Encrypted at rest;
    # never persisted or returned in plaintext.
    credentials: dict[str, str]
    config: dict[str, str] = Field(default_factory=dict)


class ConnectionPreview(BaseModel):
    """A read-only peek at what a source connection returns — proves access + reveals shape."""

    ok: bool
    count: int = 0
    raw: Any = None
    error: str | None = None


class ImportReportOut(BaseModel):
    """Completeness report for a governed import run (ADR-0004 D4)."""

    total: int
    created: list[str]
    skipped: list[str]
    failed: list[dict]
    complete: bool


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