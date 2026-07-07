"""Thin repository helpers over the ORM — the queries the runtime and API need.

Kept deliberately small (CLAUDE.md §III): add a function when a caller needs it, not before.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentInstance,
    Connection,
    EpisodeEvent,
    InstanceConnection,
    Run,
    Template,
    Tenant,
)


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


async def save_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    template_name: str,
    template_version: int,
    status: str,
    trace_ref: str | None,
    step_payloads: list[dict[str, Any]],
    instance_id: uuid.UUID | None = None,
) -> Run:
    """Persist a finished run + its ordered engine events (one EpisodeEvent per step)."""
    run = Run(
        id=run_id,
        instance_id=instance_id,
        template_name=template_name,
        template_version=template_version,
        status=status,
        trace_ref=trace_ref,
    )
    run.events = [
        EpisodeEvent(seq=i, writer="engine", kind="step", payload=payload)
        for i, payload in enumerate(step_payloads)
    ]
    session.add(run)
    await session.commit()
    return run


async def list_runs(session: AsyncSession, limit: int = 50) -> list[Run]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> Run | None:
    """Fetch a run with its ordered episode events eager-loaded (async — no lazy loading)."""
    stmt = select(Run).where(Run.id == run_id).options(selectinload(Run.events))
    return (await session.execute(stmt)).scalar_one_or_none()