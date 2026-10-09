"""Connection DTOs (ADR-0003). Credentials come IN on create and are immediately encrypted;
they never appear in any response — a summary carries only non-secret metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ConnectionCreate(BaseModel):
    type: str = "calendar"
    adapter: str = "thevea"
    # The user's own credentials (e.g. {"username": ..., "password": ...}). Encrypted at rest;
    # never persisted or returned in plaintext.
    credentials: dict[str, str]
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value):
        if "rooms" in value:
            rooms = value["rooms"]
            if not isinstance(rooms, dict) or any(
                not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) or v <= 0
                for k, v in rooms.items()
            ):
                raise ValueError("rooms must map calendar labels to positive integer Thevea IDs")
        if any(not isinstance(v, str) for k, v in value.items() if k != "rooms"):
            raise ValueError("Connection settings other than rooms must be strings")
        return value


class DoctolibLoginStart(BaseModel):
    """Start the two-step doctolib connect: username/password (+ which agendas to import). The
    headless login runs server-side; a new device then needs the emailed code (second call)."""

    username: str
    password: str
    agenda_ids: str = ""  # comma-separated, e.g. "2570190,2557171"
    label: str = ""
    # The practice's calendar labels -> Doctolib agenda ids, for a voice agent (ADR-0014). When
    # given, these are also the agendas an import reads.
    agendas: dict[str, str] = Field(default_factory=dict)


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
    # Copies in thevea the source has since moved or cancelled (orchestrator `_review`).
    review: list[dict] = []
    patients: dict[str, str] = {}  # ref -> patient name, for the failed and forced refs only
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
            review=getattr(job, "review", None) or [],
            patients=getattr(job, "patients", None) or {},
            complete=job.status == "completed",
            error=job.error,
        )


class ConnectionSummary(BaseModel):
    label: str = ""
    rooms: dict[str, int] = Field(default_factory=dict)
    # The calendar mapping under whichever key the connection's system uses (ADR-0014).
    mapping: dict[str, str] = Field(default_factory=dict)
    id: str
    type: str
    adapter: str
    created_at: datetime

    @classmethod
    def of(cls, conn) -> "ConnectionSummary":
        return cls(
            label=str((conn.config or {}).get("label", "")),
            rooms=(conn.config or {}).get("rooms", {}) if isinstance((conn.config or {}).get("rooms", {}), dict) else {},
            mapping=_mapping(conn),
            id=conn.id.hex if isinstance(conn.id, UUID) else str(conn.id),
            type=conn.type,
            adapter=conn.adapter,
            created_at=conn.created_at,
        )


def _mapping(conn) -> dict[str, str]:
    from app.workloads.conversational.calendar import VOICE_CALENDARS

    system = VOICE_CALENDARS.get(conn.adapter)
    raw = (conn.config or {}).get(system.mapping.config_key) if system else None
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


class MappingOut(BaseModel):
    config_key: str
    label: str
    numeric: bool


class CalendarSystemOut(BaseModel):
    """What the Studio needs to offer, connect and map a practice system (ADR-0014)."""

    key: str
    label: str
    connect: str
    mapping: MappingOut
    capabilities: list[str]
