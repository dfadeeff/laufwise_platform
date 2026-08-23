"""ORM models — the ADR-0002 persisted shape (relational entities + JSONB contract body).

Templates are immutable per (name, version). The template's contract (steps with kind/tools/
conditions/approval) and parameter schema live in JSONB columns; an instance's filled parameter
values likewise. Runs reference the template version they ran under (audit, ADR-0002 #15), and the
episode log is a run_id-keyed, ordered table — the "one log, two writers" substrate (#8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at, uuid_pk


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    # The Clerk organization this tenant maps to (org = tenant, ADR-0003 D2). Nullable +
    # unique: the legacy "default" tenant has none; every real tenant is keyed by its org id.
    clerk_org_id: Mapped[str | None] = mapped_column(
        String(120), unique=True, nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at()


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = created_at()


class Template(Base):
    """Immutable per (name, version). `contract` and `parameters` are the JSONB bodies the Studio
    authors (Stage 3) and the compiler reads (Stage 1)."""

    __tablename__ = "template"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_template_name_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    agent_class: Mapped[str] = mapped_column(String(40), default="conversational")
    agent_surface: Mapped[str | None] = mapped_column(String(80), nullable=True)
    risk: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | published
    contract: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()


class Connection(Base):
    """A configured StateProvider, tenant-scoped. `tokens_enc` holds Vault-encrypted OAuth tokens
    (Stage 5); for the Stage-1/2 simulated provider it stays null."""

    __tablename__ = "connection"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    type: Mapped[str] = mapped_column(String(40))  # e.g. "calendar"
    adapter: Mapped[str] = mapped_column(String(40))  # e.g. "google" | "memory"
    tokens_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    expiry: Mapped[datetime | None] = mapped_column(nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()


class AgentInstance(Base):
    """A template pinned at a version + filled parameters + bound connections + status. Deploying
    produces one of these (Stage 4); a Run references it."""

    __tablename__ = "agent_instance"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template.id"))
    template_version: Mapped[int] = mapped_column(Integer)
    param_values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | deployed | paused
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = created_at()

    connections: Mapped[list["InstanceConnection"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class InstanceConnection(Base):
    """Binds an instance's required_connections to concrete Connections (by role, e.g. 'calendar')."""

    __tablename__ = "instance_connection"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_instance.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(40), primary_key=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connection.id"))

    instance: Mapped[AgentInstance] = relationship(back_populates="connections")


class Run(Base):
    """One execution of a template (optionally via a deployed instance). Records the template
    version it ran under (ADR-0002 #15)."""

    __tablename__ = "run"

    id: Mapped[uuid.UUID] = uuid_pk()
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_instance.id"), nullable=True
    )
    template_name: Mapped[str] = mapped_column(String(200))
    template_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))  # ok | blocked | rejected
    trace_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = created_at()

    events: Mapped[list["EpisodeEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="EpisodeEvent.seq"
    )


class EpisodeEvent(Base):
    """Append-only, run_id-keyed, ordered. The shared episode log written by the engine (and, in
    Stage 6, the conversational surface). `seq` is the authoritative ordering key (ADR-0002 #8)."""

    __tablename__ = "episode_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    writer: Mapped[str] = mapped_column(String(20))  # engine | surface
    kind: Mapped[str] = mapped_column(String(20))  # step | phase
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    run: Mapped[Run] = relationship(back_populates="events")


class Approval(Base):
    """A human-in-the-loop decision point (handoff escalation or version upgrade). Stage 8 fills
    in the resolve flow; the table exists from Stage 2."""

    __tablename__ = "approval"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("run.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))  # booking_urgent | version_upgrade | ...
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|denied
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    requested_at: Mapped[datetime] = created_at()
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ImportJob(Base):
    """A background calendar-import run (ADR-0004 D4). Created when POST /import is accepted, then
    a background worker updates it incrementally so the UI can poll progress. Durable so the report
    survives the request lifetime — the client can disconnect and reconnect to a long migration
    (imagine 500 appointments) without losing it.

    The per-appointment refs live in JSONB lists; progress is len(created)+len(skipped)+len(failed)
    out of `total`. `error` is set only if the whole job crashes (a single appointment failing is
    recorded in `failed`, not here)."""

    __tablename__ = "import_job"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed
    total: Mapped[int] = mapped_column(Integer, default=0)  # eligible count (0 until known)
    created: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    skipped: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    failed: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    excluded: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # Written only after every room refused, by bypassing the destination's own working-hours
    # check (ADR-0005 D7). Its own bucket so the override is visible — these are the ones an
    # operator has to resolve by hand.
    forced: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    """Durable operational work. Lifecycle is separate from governed Run outcomes."""

    __tablename__ = "task"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    trigger_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.seq"
    )


class TaskEvent(Base):
    """Append-only task timeline used for audit, replay, and future eval assertions."""

    __tablename__ = "task_event"
    __table_args__ = (UniqueConstraint("task_id", "seq", name="uq_task_event_task_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("task.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()

    task: Mapped[Task] = relationship(back_populates="events")


class Conversation(Base):
    """One human-facing session; governed actions remain separate Run records."""

    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_instance.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="initiated", index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    started_at: Mapped[datetime] = created_at()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["ConversationEvent"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationEvent.seq",
    )


class ConversationEvent(Base):
    """Ordered surface timeline; payloads may carry latency, turns, and governed run references."""

    __tablename__ = "conversation_event"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_conversation_event_conversation_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()

    conversation: Mapped[Conversation] = relationship(back_populates="events")
