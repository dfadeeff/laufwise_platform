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


class DoctolibLoginStart(BaseModel):
    """Start the two-step doctolib connect: username/password (+ which agendas to import). The
    headless login runs server-side; a new device then needs the emailed code (second call)."""

    username: str
    password: str
    agenda_ids: str = ""  # comma-separated, e.g. "2570190,2557171"


class DoctolibCodeSubmit(BaseModel):
    code: str


class DoctolibLoginStatus(BaseModel):
    """Poll state for a login job. `connection_id` is set once the login finished and the
    connection was created; `error` is set on failure (bad credentials / wrong code / timeout)."""

    job_id: str
    status: str  # starting | awaiting_code | done | failed
    error: str | None = None
    connection_id: str | None = None


class ConnectionPreview(BaseModel):
    """A read-only peek at what a source connection returns — proves access + reveals shape."""

    ok: bool
    count: int = 0
    raw: Any = None
    error: str | None = None


class ImportJobOut(BaseModel):
    """A background import job's live state (ADR-0004 D4) — polled by the client for progress.

    `status` is running | completed | failed; `done` is the number of eligible appointments
    processed so far (created + skipped + failed) out of `total`."""

    job_id: str
    task_id: str | None = None
    status: str
    total: int
    done: int
    created: list[str]
    skipped: list[str]
    failed: list[dict]
    excluded: list[dict]  # {ref, reason} — filtered out (not confirmed / in the past), never imported
    # Written past the destination's own working-hours check because every room refused
    # (ADR-0005 D7) — the bucket the operator must review by hand.
    forced: list[str] = []
    complete: bool  # status == "completed"
    error: str | None = None  # set only if the whole job crashed

    @classmethod
    def of(cls, job) -> "ImportJobOut":
        created, skipped, failed = job.created or [], job.skipped or [], job.failed or []
        forced = getattr(job, "forced", None) or []
        return cls(
            job_id=job.id.hex if isinstance(job.id, UUID) else str(job.id),
            task_id=job.task_id.hex if getattr(job, "task_id", None) else None,
            status=job.status,
            total=job.total,
            done=len(created) + len(forced) + len(skipped) + len(failed),
            created=created,
            skipped=skipped,
            failed=failed,
            excluded=job.excluded or [],
            forced=forced,
            complete=job.status == "completed",
            error=job.error,
        )


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
