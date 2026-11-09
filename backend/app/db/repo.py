"""Thin repository helpers over the ORM — the queries the runtime and API need.

Kept deliberately small (CLAUDE.md §III): add a function when a caller needs it, not before.
"""

from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentSchedule,
    ScheduleWorker,
    BuilderRun,
    CaseRun,
    Definition,
    DefinitionVersion,
    DoctolibLogin,
    AgentInstance,
    CallerMemory,
    Conversation,
    Connection,
    ConversationEvent,
    EpisodeEvent,
    ImportJob,
    InstanceConnection,
    KnowledgeDocument,
    Run,
    ScheduleOccurrence,
    StudioAgent,
    Task,
    TaskEvent,
    Template,
    PhoneNumber,
    PriceList,
    Tenant,
    VoiceCallToken,
    VoiceChannel,
    StoredFile,
    ApprovalRequest,
    WorkflowAgent,
    WorkflowCase,
    WorkflowCaseEvent,
    WorkflowCaseFile,
    WorkspaceModule,
    WorkspaceService,
)
from app.tasks.state import TaskStatus, validate_transition


async def list_template_names(session: AsyncSession) -> list[str]:
    stmt = (
        select(Template.name)
        .where(Template.status == "published")
        .distinct()
        .order_by(Template.name)
    )
    return list((await session.execute(stmt)).scalars().all())


async def latest_published_template(session: AsyncSession, name: str) -> Template | None:
    """The highest published version of a template by name (instances pin a specific version,
    but ad-hoc runs use the latest published)."""
    stmt = (
        select(Template)
        .where(Template.name == name, Template.status == "published")
        .order_by(Template.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_template_version(
    session: AsyncSession, name: str, version: int
) -> Template | None:
    stmt = select(Template).where(Template.name == name, Template.version == version)
    return (await session.execute(stmt)).scalar_one_or_none()


# --- Stage 3: authoring (drafts, publish, versions) --------------------------------------


async def list_templates(session: AsyncSession) -> list[Template]:
    """Every template version, newest first within a name (catalog + authoring views)."""
    stmt = select(Template).order_by(Template.name, Template.version.desc())
    return list((await session.execute(stmt)).scalars().all())


async def latest_template_any_status(session: AsyncSession, name: str) -> Template | None:
    stmt = (
        select(Template)
        .where(Template.name == name)
        .order_by(Template.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_draft(session: AsyncSession, name: str) -> Template | None:
    """The (single) draft version of a template, if one exists."""
    stmt = select(Template).where(Template.name == name, Template.status == "draft")
    return (await session.execute(stmt)).scalar_one_or_none()


# --- Stage 4: configuration (tenants, connections, instances) ----------------------------


async def default_tenant(session: AsyncSession) -> Tenant:
    """The dev/test fallback tenant (used when no Clerk token is present outside production)."""
    stmt = select(Tenant).where(Tenant.name == "default").limit(1)
    tenant = (await session.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name="default")
        session.add(tenant)
        await session.flush()
        # The legacy single-practice tenant: a medical practice, as it always was (ADR-0028).
        await add_workspace_module(session, tenant.id, "praxis")
    return tenant


async def tenant_for_org(
    session: AsyncSession, clerk_org_id: str, name: str | None = None
) -> Tenant:
    """Get-or-create the tenant that maps to a Clerk organization (org = tenant, ADR-0003)."""
    stmt = select(Tenant).where(Tenant.clerk_org_id == clerk_org_id).limit(1)
    tenant = (await session.execute(stmt)).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=name or clerk_org_id, clerk_org_id=clerk_org_id)
        session.add(tenant)
        await session.flush()
    return tenant


async def workspace_module_keys(session: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """The modules a workspace runs, in the order they were added (ADR-0028)."""
    stmt = (
        select(WorkspaceModule.module_key)
        .where(WorkspaceModule.tenant_id == tenant_id)
        .order_by(WorkspaceModule.created_at, WorkspaceModule.module_key)
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_workspace_module(session: AsyncSession, tenant_id: uuid.UUID, key: str) -> None:
    """Add a module to a workspace; adding one it already runs changes nothing."""
    if key not in await workspace_module_keys(session, tenant_id):
        session.add(WorkspaceModule(tenant_id=tenant_id, module_key=key))
        await session.flush()


async def stored_file_by_hash(
    session: AsyncSession, tenant_id: uuid.UUID, sha256: str
) -> StoredFile | None:
    stmt = select(StoredFile).where(
        StoredFile.tenant_id == tenant_id, StoredFile.sha256 == sha256
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_stored_files(session: AsyncSession, tenant_id: uuid.UUID) -> list[StoredFile]:
    stmt = (
        select(StoredFile)
        .where(StoredFile.tenant_id == tenant_id)
        .order_by(StoredFile.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_stored_file(
    session: AsyncSession, file_id: uuid.UUID, tenant_id: uuid.UUID, *, lock: bool = False
) -> StoredFile | None:
    """A file only if this workspace owns it; another workspace's id reads as absent. `lock`
    holds the row until commit: an attach waits on it, so a delete cannot race one (C8)."""
    stmt = select(StoredFile).where(StoredFile.id == file_id, StoredFile.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_workflow_agents(session: AsyncSession, tenant_id: uuid.UUID) -> list[WorkflowAgent]:
    stmt = (
        select(WorkflowAgent)
        .where(WorkflowAgent.tenant_id == tenant_id)
        .order_by(WorkflowAgent.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_workflow_agent(
    session: AsyncSession, agent_id: uuid.UUID, tenant_id: uuid.UUID
) -> WorkflowAgent | None:
    stmt = select(WorkflowAgent).where(
        WorkflowAgent.id == agent_id, WorkflowAgent.tenant_id == tenant_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_workflow_cases(
    session: AsyncSession, agent_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[WorkflowCase]:
    stmt = (
        select(WorkflowCase)
        .where(WorkflowCase.agent_id == agent_id, WorkflowCase.tenant_id == tenant_id)
        .order_by(WorkflowCase.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_workflow_case(
    session: AsyncSession, case_id: uuid.UUID, tenant_id: uuid.UUID, *, lock: bool = False
) -> WorkflowCase | None:
    """`lock` serialises every change to one case until commit: two saves of one revision, or
    a computation and a save, cannot interleave (red flag C3)."""
    stmt = select(WorkflowCase).where(
        WorkflowCase.id == case_id, WorkflowCase.tenant_id == tenant_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def workflow_case_files(session: AsyncSession, case_id: uuid.UUID) -> list[StoredFile]:
    stmt = (
        select(StoredFile)
        .join(WorkflowCaseFile, WorkflowCaseFile.file_id == StoredFile.id)
        .where(WorkflowCaseFile.case_id == case_id)
        .order_by(WorkflowCaseFile.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def workflow_case_events(
    session: AsyncSession, case_id: uuid.UUID
) -> list[WorkflowCaseEvent]:
    stmt = (
        select(WorkflowCaseEvent)
        .where(WorkflowCaseEvent.case_id == case_id)
        .order_by(WorkflowCaseEvent.created_at, WorkflowCaseEvent.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def file_attached_to_case(session: AsyncSession, file_id: uuid.UUID) -> bool:
    stmt = select(WorkflowCaseFile.case_id).where(WorkflowCaseFile.file_id == file_id).limit(1)
    return (await session.execute(stmt)).first() is not None


async def case_approvals(
    session: AsyncSession, case_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[ApprovalRequest]:
    """A case's approval requests, newest first."""
    stmt = (
        select(ApprovalRequest)
        .where(ApprovalRequest.case_id == case_id, ApprovalRequest.tenant_id == tenant_id)
        .order_by(ApprovalRequest.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_approval(
    session: AsyncSession, approval_id: uuid.UUID, tenant_id: uuid.UUID
) -> ApprovalRequest | None:
    stmt = select(ApprovalRequest).where(
        ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == tenant_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def pending_approvals(session: AsyncSession, tenant_id: uuid.UUID) -> list[ApprovalRequest]:
    stmt = (
        select(ApprovalRequest)
        .where(ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == "pending")
        .order_by(ApprovalRequest.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_instances(session: AsyncSession, tenant_id: uuid.UUID) -> list[AgentInstance]:
    """A tenant's deployed instances only — the boundary is enforced in the query."""
    stmt = (
        select(AgentInstance)
        .where(AgentInstance.tenant_id == tenant_id)
        .options(selectinload(AgentInstance.connections))
        .order_by(AgentInstance.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_instance(
    session: AsyncSession, instance_id: uuid.UUID, tenant_id: uuid.UUID
) -> AgentInstance | None:
    """Fetch an instance only if it belongs to this tenant — a cross-tenant id resolves to None
    (surfaces as 404), so one tenant can never read or act on another's instance."""
    stmt = (
        select(AgentInstance)
        .where(AgentInstance.id == instance_id, AgentInstance.tenant_id == tenant_id)
        .options(selectinload(AgentInstance.connections))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_connection(
    session: AsyncSession, connection_id: uuid.UUID, tenant_id: uuid.UUID
) -> Connection | None:
    """Fetch a connection only if it belongs to this tenant (cross-tenant id -> None)."""
    stmt = select(Connection).where(
        Connection.id == connection_id, Connection.tenant_id == tenant_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_connections(session: AsyncSession, tenant_id: uuid.UUID) -> list[Connection]:
    stmt = (
        select(Connection)
        .where(Connection.tenant_id == tenant_id, Connection.removed_at.is_(None))
        .order_by(Connection.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def email_connection(session: AsyncSession, tenant_id: uuid.UUID) -> Connection | None:
    """The workspace's own mail account (ADR-0024 D3): its newest, not removed. None: none set."""
    stmt = (
        select(Connection)
        .where(
            Connection.tenant_id == tenant_id,
            Connection.type == "email",
            Connection.removed_at.is_(None),
        )
        .order_by(Connection.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def connection_in_use(session: AsyncSession, connection_id: uuid.UUID) -> list[str]:
    """What would act on this connection by itself, in words a practice can act on.

    Only things that run without a person: an agent answering calls, an armed schedule, a live
    legacy phone deployment, a running import. A paused agent or a manual workflow does not block
    removal; using it later fails loudly instead.
    """
    reasons: list[str] = []
    channels = await session.execute(
        select(StudioAgent.draft)
        .join(VoiceChannel, VoiceChannel.agent_id == StudioAgent.id)
        .where(VoiceChannel.connection_id == connection_id, VoiceChannel.active.is_(True))
    )
    for (draft,) in channels:
        reasons.append(f"{(draft or {}).get('name') or 'An agent'} answers calls with it.")
    bound = (
        select(AgentInstance)
        .join(InstanceConnection, InstanceConnection.instance_id == AgentInstance.id)
        .where(InstanceConnection.connection_id == connection_id, AgentInstance.status == "deployed")
    )
    for instance in (await session.execute(bound)).scalars():
        scheduled = select(AgentSchedule.name).where(
            AgentSchedule.target_kind == "runbook",
            AgentSchedule.target_id == instance.id,
            AgentSchedule.enabled.is_(True),
            AgentSchedule.deleted_at.is_(None),
        )
        for (name,) in await session.execute(scheduled):
            reasons.append(f"The schedule \"{name}\" runs a workflow with it.")
        if instance.phone_number and instance.agent_id is None:
            reasons.append(f"The phone deployment on {instance.phone_number} uses it.")
    running = await session.scalar(
        select(func.count(ImportJob.id))
        .join(InstanceConnection, InstanceConnection.instance_id == ImportJob.instance_id)
        .where(InstanceConnection.connection_id == connection_id, ImportJob.status == "running")
    )
    if running:
        reasons.append("An import is running with it.")
    return list(dict.fromkeys(reasons))


async def create_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    type: str,
    adapter: str,
    tokens_enc: str | None,
    config: dict[str, Any] | None = None,
) -> Connection:
    """Persist a tenant-scoped connection. `tokens_enc` is the encrypted credential (never plain)."""
    conn = Connection(
        tenant_id=tenant_id,
        type=type,
        adapter=adapter,
        tokens_enc=tokens_enc,
        config=config or {},
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def update_connection_login(
    session: AsyncSession,
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    tokens_enc: str,
    config: dict[str, Any],
) -> Connection | None:
    """Store a fresh login on an existing connection of this tenant (signing in again). The
    account it logs into does not change, so everything bound to it keeps working."""
    conn = await get_connection(session, connection_id, tenant_id)
    if conn is None or conn.removed_at is not None:
        return None
    conn.tokens_enc = tokens_enc
    conn.config = config
    await session.commit()
    await session.refresh(conn)
    return conn


async def simulated_connection(
    session: AsyncSession, tenant_id: uuid.UUID, role: str
) -> Connection:
    """Get-or-create the tenant's simulated connection for a role (the Stage-4 stand-in;
    Stage 5 replaces it with a real OAuth-backed connection the user binds explicitly)."""
    stmt = select(Connection).where(
        Connection.tenant_id == tenant_id,
        Connection.type == role,
        Connection.adapter == "memory",
    )
    conn = (await session.execute(stmt)).scalar_one_or_none()
    if conn is None:
        conn = Connection(tenant_id=tenant_id, type=role, adapter="memory")
        session.add(conn)
        await session.flush()
    return conn


async def create_instance(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    template: Template,
    param_values: dict[str, Any],
    connection_ids: dict[str, uuid.UUID],
    phone_number: str | None = None,
) -> AgentInstance:
    """Persist a deployed instance pinned to template@version, with its connection bindings."""
    instance = AgentInstance(
        tenant_id=tenant_id,
        template_id=template.id,
        template_version=template.version,
        param_values=param_values,
        status="deployed",
        phone_number=phone_number,
    )
    instance.connections = [
        InstanceConnection(role=role, connection_id=cid)
        for role, cid in sorted(connection_ids.items())
    ]
    session.add(instance)
    await session.commit()
    return instance


async def begin_run(
    session: AsyncSession, *, run_id: uuid.UUID, template_name: str, template_version: int,
    tenant_id: uuid.UUID | None, instance_id: uuid.UUID | None, case: dict,
) -> Run:
    """Commit intent before any tool acts. An incomplete run remains explicitly unverified."""
    run = Run(
        id=run_id, template_name=template_name, template_version=template_version,
        tenant_id=tenant_id, instance_id=instance_id, lifecycle="unverified",
        status="state_unavailable",
    )
    digest = hashlib.sha256(json.dumps(case, sort_keys=True, default=str).encode()).hexdigest()
    run.events = [EpisodeEvent(seq=0, writer="engine", kind="intent", payload={"input_sha256": digest})]
    session.add(run)
    await session.commit()
    return run


async def append_run_event(
    session: AsyncSession, *, run_id: uuid.UUID, tenant_id: uuid.UUID | None, kind: str, payload: dict,
) -> None:
    """Serialize independent commits to a run's append-only journal."""
    run = await session.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == tenant_id, Run.lifecycle == "unverified",
    ).with_for_update())
    if run is None:
        raise RuntimeError("execution journal is no longer writable")
    seq = (await session.scalar(select(func.max(EpisodeEvent.seq)).where(EpisodeEvent.run_id == run_id)))
    session.add(EpisodeEvent(run_id=run_id, seq=(seq if seq is not None else -1) + 1,
                             writer="engine", kind=kind, payload=payload))
    await session.commit()


async def complete_run(
    session: AsyncSession, *, run_id: uuid.UUID, tenant_id: uuid.UUID | None,
    status: str, trace_ref: str, step_payloads: list[dict],
) -> None:
    run = await session.scalar(select(Run).where(
        Run.id == run_id, Run.tenant_id == tenant_id, Run.lifecycle == "unverified",
    ).with_for_update())
    if run is None:
        raise RuntimeError("execution journal is no longer writable")
    seq = await session.scalar(select(func.max(EpisodeEvent.seq)).where(EpisodeEvent.run_id == run_id))
    for i, payload in enumerate(step_payloads, start=(seq if seq is not None else -1) + 1):
        session.add(EpisodeEvent(run_id=run_id, seq=i, writer="engine", kind="step", payload=payload))
    run.status, run.trace_ref, run.lifecycle = status, trace_ref, "completed"
    await session.commit()


async def save_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    template_name: str,
    template_version: int,
    status: TaskStatus,
    trace_ref: str | None,
    step_payloads: list[dict[str, Any]],
    instance_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> Run:
    """Persist a finished run + its ordered engine events (one EpisodeEvent per step).

    `trace` is the engine's full JSONL record (state hashes, tool calls, timestamps), stored after
    the steps as `trace` events, so the audit trail does not live only on a container's disk.
    """
    run = Run(
        id=run_id,
        instance_id=instance_id,
        tenant_id=tenant_id,
        template_name=template_name,
        template_version=template_version,
        status=status,
        trace_ref=trace_ref,
    )
    run.events = [
        EpisodeEvent(seq=i, writer="engine", kind="step", payload=payload)
        for i, payload in enumerate(step_payloads)
    ] + [
        EpisodeEvent(seq=len(step_payloads) + i, writer="engine", kind="trace", payload=record)
        for i, record in enumerate(trace or [])
    ]
    session.add(run)
    await session.commit()
    return run


async def save_conversation_run(
    session: AsyncSession, *, conversation_id: uuid.UUID, **run: Any
) -> Run | None:
    """A governed run a call made, owned by the call's practice and agent.

    The owner is read from the conversation rather than passed in, so a run can never be filed
    under a tenant the call did not belong to. None if the conversation does not exist.
    """
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return await save_run(
        session, tenant_id=conversation.tenant_id, instance_id=conversation.instance_id, **run
    )


async def list_runs(session: AsyncSession, limit: int = 50, *, tenant_id=None) -> list[Run]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(Run.tenant_id == tenant_id)
    return list((await session.execute(stmt)).scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID, *, tenant_id=None) -> Run | None:
    """Fetch a run with its ordered episode events eager-loaded (async — no lazy loading)."""
    stmt = select(Run).where(Run.id == run_id).options(selectinload(Run.events))
    if tenant_id is not None:
        stmt = stmt.where(Run.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# --- operational tasks ------------------------------------------------------------------


async def create_task(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    instance_id: uuid.UUID,
    task_type: str,
    trigger_type: str,
    context: dict[str, Any] | None = None,
) -> Task:
    task = Task(
        tenant_id=tenant_id,
        instance_id=instance_id,
        task_type=task_type,
        trigger_type=trigger_type,
        context=context or {},
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


async def list_tasks(session: AsyncSession, tenant_id: uuid.UUID) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.tenant_id == tenant_id)
        .options(selectinload(Task.events))
        .order_by(Task.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_task(
    session: AsyncSession, task_id: uuid.UUID, tenant_id: uuid.UUID
) -> Task | None:
    stmt = (
        select(Task)
        .where(Task.id == task_id, Task.tenant_id == tenant_id)
        .options(selectinload(Task.events))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --- conversations ----------------------------------------------------------------------


async def create_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    instance_id: uuid.UUID,
    channel: str,
    direction: str,
    external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant_id,
        instance_id=instance_id,
        channel=channel,
        direction=direction,
        external_id=external_id,
        metadata_=metadata or {},
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation


async def admit_voice_call(
    session: AsyncSession,
    *,
    token_hash: str,
    conversation_id: uuid.UUID,
    language: str,
    rehearsal: bool,
    expires_at: datetime,
    caller_number: str | None = None,
    recall: str | None = None,
    caller_hash: str | None = None,
    agent_id: uuid.UUID | None = None,
    calendar_mode: str = "sandbox",
    connection_id: uuid.UUID | None = None,
) -> None:
    """Record an admitted call, sweeping any admission nobody ever opened."""
    await session.execute(
        delete(VoiceCallToken).where(VoiceCallToken.expires_at <= datetime.now(timezone.utc))
    )
    session.add(
        VoiceCallToken(
            token_hash=token_hash,
            conversation_id=conversation_id,
            language=language,
            rehearsal=rehearsal,
            expires_at=expires_at,
            caller_number=caller_number,
            recall=recall,
            caller_hash=caller_hash,
            agent_id=agent_id,
            calendar_mode=calendar_mode,
            connection_id=connection_id,
        )
    )
    await session.commit()


async def redeem_voice_call(
    session: AsyncSession, token_hash: str
) -> tuple[dict[str, Any], Conversation] | None:
    """The admission and its conversation, or None if the token is unknown, used or expired.

    The DELETE is the read: two sockets presenting one token race on the same row, and exactly
    one of them gets it back.
    """
    result = await session.execute(
        delete(VoiceCallToken)
        .where(
            VoiceCallToken.token_hash == token_hash,
            VoiceCallToken.expires_at > datetime.now(timezone.utc),
        )
        .returning(*VoiceCallToken.__table__.c)
    )
    admitted = result.mappings().one_or_none()
    await session.commit()
    if admitted is None:
        return None
    conversation = await session.get(Conversation, admitted["conversation_id"])
    if conversation is None:
        return None
    return dict(admitted), conversation


async def studio_voice_instance(
    session: AsyncSession, *, tenant_id: uuid.UUID, template_name: str
) -> AgentInstance | None:
    """Get-or-create this tenant's deployed instance of the Studio voice agent.

    A conversation belongs to an instance, and an instance is pinned to `template@version` — which
    is what lets a saved call answer "which agent said this?". Without that a transcript is an
    anecdote: you can read it, but you cannot tell whether it came from the prompt you are about to
    change or the one before it. Returns None when the template has not been published yet, so the
    caller can say so rather than inventing a binding.
    """
    template = await latest_published_template(session, template_name)
    if template is None:
        return None
    existing = (
        await session.execute(
            select(AgentInstance)
            .where(
                AgentInstance.tenant_id == tenant_id,
                AgentInstance.template_id == template.id,
                AgentInstance.status == "deployed",
            )
            .options(selectinload(AgentInstance.connections))
        )
    ).scalars().first()
    if existing is not None:
        return existing
    connection = await simulated_connection(session, tenant_id=tenant_id, role="calendar")
    return await create_instance(
        session,
        tenant_id=tenant_id,
        template=template,
        param_values={},
        connection_ids={"calendar": connection.id},
    )


async def instance_for_phone_number(
    session: AsyncSession, *, phone_number: str
) -> AgentInstance | None:
    """The deployed agent that answers this number, or None.

    Deliberately NOT tenant-scoped: an inbound call carries no session, so the dialled number is
    what identifies the tenant — the instance it resolves to supplies it. That makes the number
    itself a credential of sorts, which is why the webhook that calls this refuses to run without
    a valid Twilio signature.
    """
    return (
        await session.execute(
            select(AgentInstance)
            .where(
                AgentInstance.phone_number == phone_number,
                AgentInstance.status == "deployed",
            )
            .options(selectinload(AgentInstance.connections))
        )
    ).scalars().first()


async def instance_connection(
    session: AsyncSession, *, instance_id: uuid.UUID, role: str
) -> Connection | None:
    """The Connection an instance has bound to one role, or None if the role is unbound.

    Unbound is a legitimate answer, not a missing row: a Studio instance that has never been
    pointed at a real system rehearses against the sandbox, and that has to be expressible.
    """
    return (
        await session.execute(
            select(Connection)
            .join(InstanceConnection, InstanceConnection.connection_id == Connection.id)
            .where(
                InstanceConnection.instance_id == instance_id,
                InstanceConnection.role == role,
            )
        )
    ).scalars().first()


async def instances_with_transcripts(session: AsyncSession) -> list[AgentInstance]:
    """Every instance that still holds a transcript — the sweep asks each one for its period."""
    holding = (
        select(Conversation.instance_id)
        .join(ConversationEvent, ConversationEvent.conversation_id == Conversation.id)
        .distinct()
    )
    return list(
        (await session.execute(select(AgentInstance).where(AgentInstance.id.in_(holding))))
        .scalars()
        .all()
    )


async def purge_expired_transcripts(
    session: AsyncSession, *, older_than_days: int, instance_ids: list[uuid.UUID] | None = None
) -> int:
    """Delete the stored text of every conversation that has outlived the retention period.

    The practice specification is exact about this (§4.1, §7): audio is never stored at all, and
    text transcripts live inside the protected system for a fixed number of days and are then
    deleted automatically. "Automatically" is the operative word — a retention promise that needs
    someone to remember to run a script is not a retention promise.

    The EVENTS go; the conversation row stays. That is the difference between honouring a
    retention period and destroying the audit trail: afterwards you can still see that a call
    happened, when, on which agent and how it ended — you just cannot read what was said. A
    summary email that quotes a `call_id` from five weeks ago still resolves to something.

    `instance_ids` limits the sweep to those agents, because each practice chooses its own period.

    Returns the number of conversations whose timeline was cleared.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    expired_calls = select(Conversation.id).where(Conversation.started_at < cutoff)
    if instance_ids is not None:
        expired_calls = expired_calls.where(Conversation.instance_id.in_(instance_ids))
    expired = (await session.execute(expired_calls)).scalars().all()
    if not expired:
        return 0
    await session.execute(
        delete(ConversationEvent).where(ConversationEvent.conversation_id.in_(expired))
    )
    await session.commit()
    return len(expired)


async def append_conversation_event(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """Append one ordered event to a conversation's timeline.

    `seq` continues from what is already stored rather than from an in-memory counter, so a
    reconnect or a second writer cannot silently overwrite history — the unique constraint on
    (conversation_id, seq) turns a collision into an error instead of a lost turn.
    """
    used = (
        await session.execute(
            select(func.count())
            .select_from(ConversationEvent)
            .where(ConversationEvent.conversation_id == conversation_id)
        )
    ).scalar_one()
    session.add(
        ConversationEvent(
            conversation_id=conversation_id, seq=used, kind=kind, payload=payload
        )
    )
    await session.commit()


async def end_conversation(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Close a conversation. `metadata` is merged into what the call was opened with."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return
    conversation.status = status
    if metadata:
        # A new dict, not an in-place update: JSONB mutation is invisible to the session.
        conversation.metadata_ = {**(conversation.metadata_ or {}), **metadata}
    conversation.ended_at = datetime.now(timezone.utc)
    await session.commit()


async def list_conversations(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.tenant_id == tenant_id)
        .options(selectinload(Conversation.events))
        .order_by(Conversation.started_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, tenant_id: uuid.UUID
) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
        .options(selectinload(Conversation.events))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --- import jobs (ADR-0004 D4) -----------------------------------------------------------

async def create_import_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    instance_id: uuid.UUID,
    shadow_task: bool = False,
    trigger: str = "manual",
    window: dict | None = None,
    commit: bool = True,
) -> ImportJob:
    """Queue an import job for an instance over `window`; the schedule worker takes it and fills
    in its progress. `commit=False` leaves it in the caller's transaction (a schedule commits it
    with its occurrence)."""
    task = None
    if shadow_task:
        task = Task(
            tenant_id=tenant_id,
            instance_id=instance_id,
            task_type="calendar_import",
            trigger_type=trigger,
            status="live",
            context={"legacy_import": True},
        )
        session.add(task)
        await session.flush()

    job = ImportJob(
        tenant_id=tenant_id,
        instance_id=instance_id,
        task_id=task.id if task else None,
        status="queued",
        trigger=trigger,
        window=window,
    )
    session.add(job)
    await session.flush()
    if task:
        session.add(
            TaskEvent(
                task_id=task.id,
                seq=0,
                kind="import_started",
                payload={"import_job_id": job.id.hex, "runtime": "legacy_v3"},
            )
        )
    if commit:
        await session.commit()
        await session.refresh(job)
    return job


async def finish_import_task(
    session: AsyncSession,
    job: ImportJob,
    *,
    status: str,
    summary: dict[str, int],
) -> None:
    """Finish an opt-in shadow task without changing the legacy import result."""
    if job.task_id is None:
        return
    task = await get_task(session, job.task_id, job.tenant_id)
    if task is None:
        return

    validate_transition(task.status, status)
    task.status = status
    task.events.append(
        TaskEvent(
            seq=len(task.events),
            kind=f"import_{status}",
            payload={"import_job_id": job.id.hex, **summary},
        )
    )


async def get_import_job(
    session: AsyncSession, job_id: uuid.UUID, tenant_id: uuid.UUID
) -> ImportJob | None:
    """Fetch a job only if it belongs to this tenant (cross-tenant id -> None)."""
    stmt = select(ImportJob).where(
        ImportJob.id == job_id, ImportJob.tenant_id == tenant_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_template_by_id(session: AsyncSession, template_id: uuid.UUID) -> Template | None:
    """The catalog row an instance was deployed from — its name is what decides whether a clock
    can drive it."""
    return await session.get(Template, template_id)


async def reclaim_stale_import_jobs(session: AsyncSession, *, older_than_minutes: int) -> int:
    """Mark jobs still `running` past `older_than_minutes` as `interrupted`, and return how many.

    A process restart mid-run leaves a job `running` forever (app/sync/jobs.py says so itself).
    Together with the concurrency guard below, ONE such orphan would block every later tick
    permanently — and invisibly, because the only symptom is a website slowly going stale. The
    scheduler calls this before it consults the guard (ADR-0010 D5).

    `updated_at` is what moves: the worker writes progress after every unit, so a live job is
    never stale, however long the whole run takes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    stmt = select(ImportJob).where(ImportJob.status == "running", ImportJob.updated_at < cutoff)
    stale = list((await session.execute(stmt)).scalars().all())
    for job in stale:
        _hand_back(job, f"no progress for {older_than_minutes} min")
    if stale:
        await session.commit()
    return len(stale)


# How often an import is started before a worker gives up on it. Running it again is safe: the
# contract skips what is already written (calendar_import, availability_mirror preconditions).
MAX_IMPORT_ATTEMPTS = 3


def _hand_back(job: ImportJob, why: str) -> None:
    """A job its worker could not finish: queued again for the next worker while it has attempts
    left (and knows its window), otherwise interrupted."""
    job.locked_by = None
    if job.window is not None and job.attempt < MAX_IMPORT_ATTEMPTS:
        job.status, job.error = "queued", f"{why}; started again (attempt {job.attempt + 1})"
    else:
        job.status, job.error = "interrupted", f"{why}; not started again"


async def claim_queued_import_jobs(session: AsyncSession, node: str, limit: int) -> list[ImportJob]:
    """Take up to `limit` queued jobs, oldest first, for this worker. Two workers never take the
    same one (SKIP LOCKED); the caller commits, then starts them."""
    if limit <= 0:
        return []
    stmt = (
        select(ImportJob)
        .where(ImportJob.status == "queued")
        .order_by(ImportJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.execute(stmt)).scalars())
    for job in jobs:
        if job.window is None:  # queued before jobs carried their window: it cannot run here
            job.status, job.error = "interrupted", "queued without its days; start it again"
            continue
        job.status, job.locked_by, job.attempt = "running", node, job.attempt + 1
    return [j for j in jobs if j.status == "running"]


async def import_job_holder(session: AsyncSession, job_id: uuid.UUID) -> str | None:
    """Which worker holds the job now: a run whose worker no longer holds it stops writing."""
    stmt = select(ImportJob.locked_by).where(ImportJob.id == job_id, ImportJob.status == "running")
    return (await session.execute(stmt)).scalar_one_or_none()


async def running_import_job_for_instance(
    session: AsyncSession, instance_id: uuid.UUID, tenant_id: uuid.UUID
) -> ImportJob | None:
    """The instance's currently-running import job, if any — the concurrency guard so a second
    trigger returns the in-flight job instead of starting a duplicate run."""
    stmt = select(ImportJob).where(
        ImportJob.instance_id == instance_id,
        ImportJob.tenant_id == tenant_id,
        ImportJob.status == "running",
    ).order_by(ImportJob.created_at.desc())
    return (await session.execute(stmt)).scalars().first()


async def ensure_call_followup(session, conversation_id, summary, delivery):
    """Idempotent staff work; sensitive call details stay in the retained conversation."""
    conversation = (await session.scalars(select(Conversation).where(
        Conversation.id == conversation_id).with_for_update())).first()
    if conversation is None or (conversation.metadata_ or {}).get("mode") == "rehearsal":
        return
    if not summary.get("staff_action_required") and delivery.get("sent"):
        return
    task_id = uuid.uuid5(conversation.id, "staff-followup")
    if await session.get(Task, task_id):
        return
    task = Task(id=task_id, tenant_id=conversation.tenant_id, instance_id=conversation.instance_id,
        task_type="call_followup", trigger_type="conversation", status="pending",
        context={"conversation_id":conversation.id.hex, "reason":"callback" if summary.get("staff_action_required") else "notification_failed"},
        events=[TaskEvent(seq=1,kind="created",payload={"source":"call_summary"})])
    session.add(task)
    await session.commit()


async def resolve_call_followup(session, task_id, tenant_id, *, actor, complete):
    task = (await session.scalars(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id,
        Task.task_type == "call_followup").with_for_update().options(selectinload(Task.events)))).first()
    if task is None:
        return None
    if task.status == "completed":
        return task
    current_owner = (task.context or {}).get("assigned_to")
    if current_owner and current_owner != actor:
        raise ValueError("This callback is already assigned to another team member.")
    task.context = {**task.context, "assigned_to":actor}
    if task.status == "pending":
        validate_transition(task.status, "live")
        task.status = "live"
        task.events.append(TaskEvent(seq=len(task.events)+1, kind="claimed", payload={"actor":actor}))
    if complete:
        validate_transition(task.status, "completed")
        task.status = "completed"
        task.events.append(TaskEvent(seq=len(task.events)+1, kind="completed", payload={"actor":actor}))
    await session.commit()
    return task


# --- caller memory (ADR-0011) ------------------------------------------------------------------


async def recall_caller(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID, caller_hash: str
) -> CallerMemory | None:
    """The row for one caller of one agent. Scoped by tenant AND agent, so memory never pools."""
    stmt = select(CallerMemory).where(
        CallerMemory.tenant_id == tenant_id,
        CallerMemory.agent_id == agent_id,
        CallerMemory.caller_hash == caller_hash,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def remember_caller(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    caller_hash: str,
    projection: dict[str, Any],
) -> CallerMemory:
    """Write what the call VERIFIED. `projection` is built by the booking session, which returns
    nothing at all unless a date-of-birth check actually passed (ADR-0011 D5)."""
    row = await recall_caller(
        session, tenant_id=tenant_id, agent_id=agent_id, caller_hash=caller_hash
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = CallerMemory(
            tenant_id=tenant_id, agent_id=agent_id, caller_hash=caller_hash, call_count=0
        )
        session.add(row)
    row.patient_id = projection.get("patient_id") or row.patient_id
    row.display_name = projection.get("display_name") or row.display_name
    row.locale = projection.get("locale") or row.locale
    row.last_outcome = projection.get("last_outcome")
    row.call_count = (row.call_count or 0) + 1
    row.last_seen_at = now
    if projection.get("verified"):
        row.verified_at = now
    await session.commit()
    return row


async def purge_expired_caller_memory(session: AsyncSession, *, older_than_days: int) -> int:
    """Forget callers nobody has heard from in a long time. Returns how many were forgotten."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    result = await session.execute(
        delete(CallerMemory).where(CallerMemory.last_seen_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0


async def forget_callers(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> int:
    """Erasure on request (Art. 17): everything one agent remembers about everyone."""
    result = await session.execute(
        delete(CallerMemory).where(
            CallerMemory.tenant_id == tenant_id, CallerMemory.agent_id == agent_id
        )
    )
    await session.commit()
    return result.rowcount or 0


async def runs_for_conversation(
    session: AsyncSession, conversation, *, tenant_id: uuid.UUID
) -> list[Run]:
    """The governed runs a call produced, newest last, with their steps.

    The link already exists: a `tool_call` event carries the `run_id` of the run its tool started.
    This follows it, scoped by tenant like every other read, so one call's checks cannot be read
    from another practice's runs.
    """
    ids = {
        event.payload.get("run_id")
        for event in conversation.events
        if event.kind == "tool_call" and event.payload.get("run_id")
    }
    if not ids:
        return []
    parsed = set()
    for value in ids:
        try:
            parsed.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return []
    stmt = (
        select(Run)
        .where(Run.id.in_(parsed), Run.tenant_id == tenant_id)
        .options(selectinload(Run.events))
        .order_by(Run.started_at)
    )
    return list((await session.execute(stmt)).scalars().all())


# --- phone numbers (the claimable pool) -----------------------------------------------------------


async def phone_number_owners(session: AsyncSession) -> dict[str, str]:
    """Every claimed number and the tenant that owns it. The pool is the account minus these."""
    rows = await session.execute(select(PhoneNumber.number, PhoneNumber.tenant_id))
    return {number: str(tenant_id) for number, tenant_id in rows}


async def phone_numbers_of(session: AsyncSession, tenant_id) -> list[str]:
    rows = await session.scalars(
        select(PhoneNumber.number)
        .where(PhoneNumber.tenant_id == tenant_id)
        .order_by(PhoneNumber.claimed_at)
    )
    return list(rows)


async def add_phone_number(
    session: AsyncSession, *, number: str, tenant_id, twilio_sid: str
) -> None:
    """Claim a number. Raises IntegrityError if another practice got there first."""
    session.add(PhoneNumber(number=number, tenant_id=tenant_id, twilio_sid=twilio_sid))
    await session.flush()


async def remove_phone_number(session: AsyncSession, number: str) -> None:
    await session.execute(delete(PhoneNumber).where(PhoneNumber.number == number))


async def phone_number_in_use(session: AsyncSession, number: str) -> bool:
    """Whether an agent's channel holds this number, live or paused."""
    found = await session.scalars(
        select(VoiceChannel.id).where(VoiceChannel.phone_number == number).limit(1)
    )
    return found.first() is not None


async def count_conversations_since(session: AsyncSession, tenant_id, since: datetime) -> int:
    """How many calls a practice had since `since` — the overview's sense of activity."""
    return (
        await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.tenant_id == tenant_id, Conversation.started_at >= since
            )
        )
    ) or 0


# --- knowledge documents (ADR-0017) ---------------------------------------------------------------


async def list_knowledge(session: AsyncSession, tenant_id) -> list[KnowledgeDocument]:
    rows = await session.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .order_by(KnowledgeDocument.created_at)
    )
    return list(rows)


async def get_knowledge(session: AsyncSession, document_id, tenant_id) -> KnowledgeDocument | None:
    """One document, only if it belongs to this workspace: another's id is simply not found."""
    return (
        await session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == tenant_id
            )
        )
    ).first()


async def add_knowledge(
    session: AsyncSession, *, tenant_id, title: str, source: str, content: str
) -> KnowledgeDocument:
    document = KnowledgeDocument(tenant_id=tenant_id, title=title, source=source, content=content)
    session.add(document)
    await session.flush()
    return document


async def knowledge_by_ids(session: AsyncSession, tenant_id, ids: list[str]) -> list[KnowledgeDocument]:
    """The workspace's documents among `ids`, in the order given. Unknown ids are left out."""
    parsed = []
    for value in ids:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return []
    found = {
        d.id: d
        for d in await session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.id.in_(parsed)
            )
        )
    }
    return [found[i] for i in parsed if i in found]


# --- a workspace's service catalogue (ADR-0025) ---------------------------------------------


async def list_services(session: AsyncSession, tenant_id) -> list[WorkspaceService]:
    rows = await session.scalars(
        select(WorkspaceService)
        .where(WorkspaceService.tenant_id == tenant_id)
        .order_by(WorkspaceService.position, WorkspaceService.created_at)
    )
    return list(rows)


async def get_service(session: AsyncSession, service_id, tenant_id) -> WorkspaceService | None:
    """One service, only if it belongs to this workspace: another's id is simply not found."""
    return (
        await session.scalars(
            select(WorkspaceService).where(
                WorkspaceService.id == service_id, WorkspaceService.tenant_id == tenant_id
            )
        )
    ).first()


async def add_service(session: AsyncSession, *, tenant_id, **fields: Any) -> WorkspaceService:
    row = WorkspaceService(tenant_id=tenant_id, **fields)
    session.add(row)
    await session.flush()
    return row


# --- website prices (ADR-0032) ------------------------------------------------------------------


async def get_price_list(session: AsyncSession, tenant_id: uuid.UUID) -> PriceList | None:
    return await session.get(PriceList, tenant_id)


async def set_price_list_url(session: AsyncSession, tenant_id: uuid.UUID, url: str) -> PriceList:
    """Point the workspace at a price page. Changing the page keeps the rows already read: a
    service linked to one keeps its last price until the new page is read."""
    row = await session.get(PriceList, tenant_id)
    if row is None:
        row = PriceList(tenant_id=tenant_id, url=url, rows=[], error="")
        session.add(row)
    else:
        row.url = url
    await session.flush()
    return row


async def price_lists(session: AsyncSession) -> list[PriceList]:
    """Every workspace's price list, for the hourly refresh."""
    return list((await session.execute(select(PriceList))).scalars())


# --- workflow definitions as versioned data (ADR-0039 step 2b) -----------------------------


async def get_definition(session: AsyncSession, key: str, *, lock: bool = False) -> Definition | None:
    """`lock` serialises the creation of a definition's next version until commit."""
    stmt = select(Definition).where(Definition.key == key)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_definition_version(
    session: AsyncSession, definition_id: uuid.UUID, version: int
) -> DefinitionVersion | None:
    stmt = select(DefinitionVersion).where(
        DefinitionVersion.definition_id == definition_id, DefinitionVersion.version == version
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def definition_draft(session: AsyncSession, definition_id: uuid.UUID) -> DefinitionVersion | None:
    stmt = select(DefinitionVersion).where(
        DefinitionVersion.definition_id == definition_id, DefinitionVersion.status == "draft"
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def highest_definition_version(session: AsyncSession, definition_id: uuid.UUID) -> int:
    stmt = select(func.max(DefinitionVersion.version)).where(
        DefinitionVersion.definition_id == definition_id
    )
    return (await session.execute(stmt)).scalar_one() or 0


async def latest_published_of(session: AsyncSession, definition_id: uuid.UUID) -> DefinitionVersion | None:
    stmt = (
        select(DefinitionVersion)
        .where(DefinitionVersion.definition_id == definition_id, DefinitionVersion.status == "published")
        .order_by(DefinitionVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def definition_versions(session: AsyncSession, definition_id: uuid.UUID) -> list[DefinitionVersion]:
    stmt = (
        select(DefinitionVersion)
        .where(DefinitionVersion.definition_id == definition_id)
        .order_by(DefinitionVersion.version.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def definitions_of(session: AsyncSession, tenant_id: uuid.UUID | None) -> list[Definition]:
    """A workspace's own definitions, or (None) the platform's shipped templates."""
    owner = Definition.tenant_id.is_(None) if tenant_id is None else Definition.tenant_id == tenant_id
    stmt = select(Definition).where(owner).order_by(Definition.created_at, Definition.key)
    return list((await session.execute(stmt)).scalars())


async def latest_published_versions(session: AsyncSession) -> list[tuple[Definition, DefinitionVersion]]:
    """Each definition with its newest published version; a definition with none is left out."""
    newest = (
        select(DefinitionVersion.definition_id, func.max(DefinitionVersion.version).label("version"))
        .where(DefinitionVersion.status == "published")
        .group_by(DefinitionVersion.definition_id)
        .subquery()
    )
    stmt = (
        select(Definition, DefinitionVersion)
        .join(DefinitionVersion, DefinitionVersion.definition_id == Definition.id)
        .join(
            newest,
            (newest.c.definition_id == DefinitionVersion.definition_id)
            & (newest.c.version == DefinitionVersion.version),
        )
        .order_by(Definition.key)
    )
    return [(d, v) for d, v in (await session.execute(stmt)).all()]


async def expired_waits(session: AsyncSession, now: datetime) -> list[CaseRun]:
    """Runs held at a wait whose date has passed, across every tenant: only the sweep asks."""
    stmt = select(CaseRun).where(CaseRun.state == "suspended", CaseRun.expires_at <= now)
    return list((await session.execute(stmt)).scalars())


async def latest_case_run(session: AsyncSession, case_id: uuid.UUID) -> CaseRun | None:
    stmt = (
        select(CaseRun)
        .where(CaseRun.case_id == case_id)
        .order_by(CaseRun.started_at.desc(), CaseRun.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def builder_runs(session: AsyncSession, definition_id: uuid.UUID) -> list[BuilderRun]:
    stmt = (
        select(BuilderRun)
        .where(BuilderRun.definition_id == definition_id)
        .order_by(BuilderRun.created_at.desc(), BuilderRun.id.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def active_builder_runs(session: AsyncSession) -> list[BuilderRun]:
    """Lock candidates while reclaiming; the caller must check the lease, not process startup."""
    stmt = select(BuilderRun).where(BuilderRun.state.in_(("queued", "running"))).with_for_update()
    return list((await session.execute(stmt)).scalars())


async def claim_builder_run(session, run_id, owner_id, lease_seconds):
    now = datetime.now(timezone.utc)
    result = await session.execute(update(BuilderRun).where(
        BuilderRun.id == run_id, BuilderRun.state == "queued",
        BuilderRun.lease_expires_at > now,
    ).values(state="running", owner_id=owner_id, lease_expires_at=now + timedelta(seconds=lease_seconds)))
    await session.commit()
    return bool(result.rowcount)


async def renew_builder_lease(session, run_id, owner_id, lease_seconds):
    now = datetime.now(timezone.utc)
    result = await session.execute(update(BuilderRun).where(
        BuilderRun.id == run_id, BuilderRun.owner_id == owner_id,
        BuilderRun.state == "running", BuilderRun.lease_expires_at > now,
    ).values(lease_expires_at=now + timedelta(seconds=lease_seconds)))
    await session.commit()
    return bool(result.rowcount)


async def owned_builder_run(session, run_id, owner_id):
    return await session.scalar(select(BuilderRun).where(
        BuilderRun.id == run_id, BuilderRun.owner_id == owner_id,
        BuilderRun.state == "running", BuilderRun.lease_expires_at > datetime.now(timezone.utc),
    ).with_for_update())


# --- schedules (ADR-0040) --------------------------------------------------------------------
# The worker's discovery queries cross workspaces: the clock acts for nobody, and every
# occurrence carries its workspace into the run. Everything a request reads is tenant-scoped.

_LIVE_OCCURRENCE = ("claimed",)


async def get_schedule(
    session: AsyncSession, schedule_id: uuid.UUID, tenant_id: uuid.UUID, *, lock: bool = False
) -> AgentSchedule | None:
    stmt = select(AgentSchedule).where(
        AgentSchedule.id == schedule_id,
        AgentSchedule.tenant_id == tenant_id,
        AgentSchedule.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def schedule_run_as(session: AsyncSession, schedule_id: uuid.UUID) -> str | None:
    stmt = select(AgentSchedule.run_as).where(AgentSchedule.id == schedule_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    return await session.get(Tenant, tenant_id)


async def due_schedules(session: AsyncSession, now: datetime, limit: int) -> list[AgentSchedule]:
    """Enabled schedules whose time has come, locked; another worker skips the ones held here."""
    stmt = (
        select(AgentSchedule)
        .where(
            AgentSchedule.enabled.is_(True),
            AgentSchedule.deleted_at.is_(None),
            AgentSchedule.next_run_at <= now,
        )
        .order_by(AgentSchedule.next_run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars())


async def add_occurrence(session: AsyncSession, **values: Any) -> uuid.UUID | None:
    """Record one due time; its id, or None when it is already recorded: at most one per due
    time."""
    stmt = (
        pg_insert(ScheduleOccurrence)
        .values(id=uuid.uuid4(), **values)
        .on_conflict_do_nothing(constraint="uq_schedule_occurrence_due")
        .returning(ScheduleOccurrence.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def cancel_queued_occurrences(
    session: AsyncSession, schedule_id: uuid.UUID, now: datetime, why: str
) -> None:
    await session.execute(
        update(ScheduleOccurrence)
        .where(ScheduleOccurrence.schedule_id == schedule_id, ScheduleOccurrence.status == "queued")
        .values(status="cancelled", finished_at=now, error=why)
    )


async def lock_workspace_schedules(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Serialise changes to how many schedules a workspace runs, until the transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"schedules-of:{tenant_id}"}
    )


async def reclaim_expired_occurrences(
    session: AsyncSession, now: datetime, max_attempts: int
) -> None:
    """A claim whose lease ran out belongs to a worker that is gone: queue it again, or give up
    after `max_attempts`. A worker still working holds its row locked, so it is skipped here,
    never re-queued under it, and no tick waits for it."""
    expired = (ScheduleOccurrence.status == "claimed") & (ScheduleOccurrence.locked_until < now)
    ids = list(
        (
            await session.execute(
                select(ScheduleOccurrence.id).where(expired).with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not ids:
        return
    expired = ScheduleOccurrence.id.in_(ids)
    await session.execute(
        update(ScheduleOccurrence)
        .where(expired, ScheduleOccurrence.attempt >= max_attempts)
        .values(
            status="failed", finished_at=now, locked_by=None, locked_until=None,
            error=f"the worker running it stopped {max_attempts} times; not tried again",
        )
    )
    await session.execute(
        update(ScheduleOccurrence)
        .where(expired, ScheduleOccurrence.attempt < max_attempts)
        .values(status="queued", locked_by=None, locked_until=None)
    )


async def tenants_with_queued_occurrences(session: AsyncSession) -> list[uuid.UUID]:
    """Workspaces with work waiting, the longest-waiting first: the worker takes one of each in
    turn, so no workspace waits behind another's backlog."""
    stmt = (
        select(ScheduleOccurrence.tenant_id)
        .where(ScheduleOccurrence.status == "queued")
        .group_by(ScheduleOccurrence.tenant_id)
        .order_by(func.min(ScheduleOccurrence.scheduled_for))
    )
    return list((await session.execute(stmt)).scalars())


async def next_queued_occurrence(session: AsyncSession, tenant_id: uuid.UUID) -> ScheduleOccurrence | None:
    stmt = (
        select(ScheduleOccurrence)
        .where(ScheduleOccurrence.tenant_id == tenant_id, ScheduleOccurrence.status == "queued")
        .order_by(ScheduleOccurrence.scheduled_for)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_target(session: AsyncSession, target_id: uuid.UUID) -> None:
    """Serialise claims on one agent until the transaction ends, across workers."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"schedule-target:{target_id}"}
    )


async def target_is_running(session: AsyncSession, target_id: uuid.UUID, now: datetime) -> bool:
    """Whether a live claim already runs this agent (any of its schedules)."""
    stmt = select(func.count()).where(
        ScheduleOccurrence.target_id == target_id,
        ScheduleOccurrence.status.in_(_LIVE_OCCURRENCE),
        ScheduleOccurrence.locked_until >= now,
    )
    return (await session.execute(stmt)).scalar_one() > 0


async def claimed_occurrence(
    session: AsyncSession, occurrence_id: uuid.UUID, node: str
) -> ScheduleOccurrence | None:
    """The occurrence, locked, if this worker still holds it."""
    stmt = (
        select(ScheduleOccurrence)
        .where(
            ScheduleOccurrence.id == occurrence_id,
            ScheduleOccurrence.status == "claimed",
            ScheduleOccurrence.locked_by == node,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def published_version_of(
    session: AsyncSession, version_id: uuid.UUID, key: str, tenant_id: uuid.UUID
) -> tuple[Definition, DefinitionVersion] | None:
    """A published version of the definition `key`, if this workspace may run it: its own, or a
    shipped template."""
    stmt = (
        select(Definition, DefinitionVersion)
        .join(DefinitionVersion, DefinitionVersion.definition_id == Definition.id)
        .where(
            DefinitionVersion.id == version_id,
            DefinitionVersion.status == "published",
            Definition.key == key,
            (Definition.tenant_id.is_(None)) | (Definition.tenant_id == tenant_id),
        )
    )
    row = (await session.execute(stmt)).one_or_none()
    return (row[0], row[1]) if row else None


async def list_schedules(
    session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID | None = None
) -> list[AgentSchedule]:
    """A workspace's schedules, soonest first; paused ones after, by name."""
    stmt = select(AgentSchedule).where(
        AgentSchedule.tenant_id == tenant_id, AgentSchedule.deleted_at.is_(None)
    )
    if target_id is not None:
        stmt = stmt.where(AgentSchedule.target_id == target_id)
    stmt = stmt.order_by(AgentSchedule.next_run_at.asc().nulls_last(), AgentSchedule.name)
    return list((await session.execute(stmt)).scalars())


async def active_schedule_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        AgentSchedule.tenant_id == tenant_id,
        AgentSchedule.enabled.is_(True),
        AgentSchedule.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one()


async def schedule_occurrences(
    session: AsyncSession, schedule_id: uuid.UUID, tenant_id: uuid.UUID, limit: int = 50
) -> list[ScheduleOccurrence]:
    """A schedule's history, newest first."""
    stmt = (
        select(ScheduleOccurrence)
        .where(ScheduleOccurrence.schedule_id == schedule_id, ScheduleOccurrence.tenant_id == tenant_id)
        .order_by(ScheduleOccurrence.scheduled_for.desc(), ScheduleOccurrence.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def latest_published_version(
    session: AsyncSession, key: str, tenant_id: uuid.UUID
) -> DefinitionVersion | None:
    """The newest published version of the definition `key`, if this workspace may run it."""
    stmt = (
        select(DefinitionVersion)
        .join(Definition, DefinitionVersion.definition_id == Definition.id)
        .where(
            Definition.key == key,
            DefinitionVersion.status == "published",
            (Definition.tenant_id.is_(None)) | (Definition.tenant_id == tenant_id),
        )
        .order_by(DefinitionVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def definition_version_number(session: AsyncSession, version_id: uuid.UUID) -> int | None:
    stmt = select(DefinitionVersion.version).where(DefinitionVersion.id == version_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def worker_ticked(session: AsyncSession, node: str, now: datetime) -> None:
    """Record that this worker is alive; forget workers gone for a day."""
    stmt = (
        pg_insert(ScheduleWorker)
        .values(node=node, started_at=now, last_tick_at=now)
        .on_conflict_do_update(index_elements=["node"], set_={"last_tick_at": now})
    )
    await session.execute(stmt)
    await session.execute(delete(ScheduleWorker).where(ScheduleWorker.last_tick_at < now - timedelta(days=1)))


async def last_worker_tick(session: AsyncSession) -> datetime | None:
    return (await session.execute(select(func.max(ScheduleWorker.last_tick_at)))).scalar_one_or_none()


# --- one runbook of a kind at a time, per workspace (ADR-0040 D9) ---------------------------
# A redeploy makes a new deployment and leaves the old one running, so "one at a time" is per
# workspace and template, not per deployment: two imports into the same thevea at once would each
# pass the absence precondition and write the same appointment twice.


def _of_template(tenant_id: uuid.UUID, template_name: str):
    return (
        select(AgentInstance.id)
        .join(Template, Template.id == AgentInstance.template_id)
        .where(AgentInstance.tenant_id == tenant_id, Template.name == template_name)
    )


async def lock_runbook(session: AsyncSession, tenant_id: uuid.UUID, template_name: str) -> None:
    """Serialise starting this workspace's runs of one runbook until the transaction ends."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"runbook:{tenant_id}:{template_name}"},
    )


async def running_import_job_for_template(
    session: AsyncSession, tenant_id: uuid.UUID, template_name: str
) -> ImportJob | None:
    """A queued or running job of any of this workspace's deployments of the template."""
    stmt = select(ImportJob).where(
        ImportJob.tenant_id == tenant_id,
        ImportJob.status.in_(("queued", "running")),
        ImportJob.instance_id.in_(_of_template(tenant_id, template_name)),
    )
    return (await session.execute(stmt)).scalars().first()


async def hand_back_import_jobs(session: AsyncSession, job_ids: list[uuid.UUID], node: str) -> None:
    """Jobs this stopping worker could not finish: handed to the next worker now, rather than
    left `running` until the stale-job sweep."""
    if not job_ids:
        return
    stmt = select(ImportJob).where(
        ImportJob.id.in_(job_ids), ImportJob.status == "running", ImportJob.locked_by == node
    )
    for job in (await session.execute(stmt)).scalars():
        _hand_back(job, "the worker running it stopped")


# --- doctolib sign-ins (app/connections/doctolib_login_jobs.py) ------------------------------------


# A sign-in whose browser a worker has open.
_BROWSER_OPEN = frozenset({"starting", "awaiting_code"})


async def add_doctolib_login(session: AsyncSession, row: DoctolibLogin) -> None:
    session.add(row)
    await session.flush()


async def get_doctolib_login(
    session: AsyncSession, login_id: uuid.UUID, tenant_id: uuid.UUID, *, lock: bool = False
) -> DoctolibLogin | None:
    stmt = select(DoctolibLogin).where(DoctolibLogin.id == login_id, DoctolibLogin.tenant_id == tenant_id)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def claim_queued_doctolib_logins(session: AsyncSession, node: str, limit: int) -> list[DoctolibLogin]:
    """Take up to `limit` queued sign-ins, oldest first, for this worker's browsers."""
    if limit <= 0:
        return []
    stmt = (
        select(DoctolibLogin)
        .where(DoctolibLogin.status == "queued")
        .order_by(DoctolibLogin.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.execute(stmt)).scalars())
    for row in rows:
        row.status, row.locked_by = "starting", node
    return rows


async def held_doctolib_login(session: AsyncSession, login_id: uuid.UUID, node: str) -> DoctolibLogin | None:
    """The sign-in, while this worker's browser is still running it."""
    stmt = select(DoctolibLogin).where(
        DoctolibLogin.id == login_id,
        DoctolibLogin.locked_by == node,
        DoctolibLogin.status.in_(_BROWSER_OPEN),
    ).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def orphaned_doctolib_logins(session: AsyncSession, alive_since: datetime) -> list[DoctolibLogin]:
    """Sign-ins whose browser's worker has not ticked since `alive_since`."""
    alive = select(ScheduleWorker.node).where(ScheduleWorker.last_tick_at >= alive_since)
    stmt = select(DoctolibLogin).where(
        DoctolibLogin.status.in_(_BROWSER_OPEN),
        DoctolibLogin.locked_by.not_in(alive),
    ).with_for_update(skip_locked=True)
    return list((await session.execute(stmt)).scalars())


async def unfinished_doctolib_logins(session: AsyncSession, before: datetime) -> list[DoctolibLogin]:
    """Sign-ins started before `before` that are still open or still hold a secret."""
    stmt = select(DoctolibLogin).where(
        DoctolibLogin.created_at < before,
        (DoctolibLogin.status.in_(("queued", "starting", "awaiting_code", "finishing")))
        | DoctolibLogin.secrets_enc.is_not(None)
        | DoctolibLogin.code_enc.is_not(None)
        | DoctolibLogin.result_enc.is_not(None),
    ).with_for_update(skip_locked=True)
    return list((await session.execute(stmt)).scalars())
