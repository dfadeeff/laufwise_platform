"""Instance endpoints (Stage 4) — deploy from the catalog, list, pause, trigger a run.

Deploying pins template@version, validates param_values against the template's parameter
schema (the auto-rendered form's server-side truth), and binds required_connections — in v1
each unbound role falls back to the tenant's simulated connection (Stage 5 replaces this
with the real OAuth flow).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant, get_runtime
from app.config import settings
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import AgentInstance, Tenant
from app.db.session import get_session
from app.instances.deploy import validate_param_values
from app.schemas.connection import ImportJobOut
from app.schemas.instance import DeployRequest, InstanceRunRequest, InstanceSummary
from app.schemas.run import RunResult
from app.sync.jobs import spawn_import_job
from app.templates.contract import TemplateContract

router = APIRouter()


@router.post("", response_model=InstanceSummary)
async def deploy_instance(
    req: DeployRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> InstanceSummary:
    template = (
        await repo.get_template_version(session, req.template, req.version)
        if req.version is not None
        else await repo.latest_published_template(session, req.template)
    )
    if template is None or template.status != "published":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no published template '{req.template}'"
            + (f" v{req.version}" if req.version is not None else ""),
        )

    contract = TemplateContract.model_validate(template.contract)
    violations = validate_param_values(contract, req.param_values)
    unknown_roles = set(req.connections) - set(contract.required_connections)
    if unknown_roles:
        violations.append(
            f"unknown connection roles {sorted(unknown_roles)} "
            f"(required: {contract.required_connections})"
        )
    if violations:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "deploy refused", "violations": violations},
        )

    connection_ids: dict[str, uuid.UUID] = {}
    for role in contract.required_connections:
        if role in req.connections:
            connection_ids[role] = uuid.UUID(req.connections[role])
        else:
            simulated = await repo.simulated_connection(session, tenant.id, role)
            connection_ids[role] = simulated.id

    instance = await repo.create_instance(
        session,
        tenant_id=tenant.id,
        template=template,
        param_values=req.param_values,
        connection_ids=connection_ids,
        phone_number=req.phone_number,
    )
    return _summary(instance, template.name)


@router.get("", response_model=list[InstanceSummary])
async def list_instances(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[InstanceSummary]:
    instances = await repo.list_instances(session, tenant.id)
    names = {t.id: t.name for t in await repo.list_templates(session)}
    return [_summary(i, names.get(i.template_id, "?")) for i in instances]


@router.post("/{instance_id}/pause", response_model=InstanceSummary)
async def pause_instance(
    instance_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> InstanceSummary:
    instance = await _resolve(session, instance_id, tenant)
    instance.status = "paused"
    await session.commit()
    return await _summary_with_name(session, instance)


@router.post("/{instance_id}/runs", response_model=RunResult)
async def run_instance(
    instance_id: str,
    req: InstanceRunRequest,
    runtime: Runtime = Depends(get_runtime),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> RunResult:
    """Manually trigger a governed run of a deployed instance (the Stage-4 done-when)."""
    instance = await _resolve(session, instance_id, tenant)
    if instance.status != "deployed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"instance is {instance.status}, not deployed"
        )
    return await runtime.run_instance(session, instance, req.case)


@router.post("/{instance_id}/import", response_model=ImportJobOut, status_code=status.HTTP_202_ACCEPTED)
async def import_appointments(
    instance_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ImportJobOut:
    """Start a governed calendar import (ADR-0004) as a BACKGROUND job and return immediately with
    its id — the migration can be long (imagine 500 appointments), so the client polls
    `GET /import/{job_id}` for progress rather than holding the request open. A concurrency guard
    returns the in-flight job if one is already running for this instance (no duplicate run)."""
    instance = await _resolve(session, instance_id, tenant)
    if instance.status != "deployed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"instance is {instance.status}, not deployed"
        )
    running = await repo.running_import_job_for_instance(session, instance.id, tenant.id)
    if running is not None:
        return ImportJobOut.of(running)

    window = {
        "from": instance.param_values.get("window_from"),
        "to": instance.param_values.get("window_to"),
    }
    job = await repo.create_import_job(
        session,
        tenant_id=tenant.id,
        instance_id=instance.id,
        shadow_task=settings.task_shadow_enabled,
    )
    spawn_import_job(job.id, instance.id, tenant.id, window)
    return ImportJobOut.of(job)


@router.get("/{instance_id}/import/{job_id}", response_model=ImportJobOut)
async def get_import_job(
    instance_id: str,
    job_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ImportJobOut:
    """Poll a background import job's live progress + final report (ADR-0004 D4)."""
    try:
        jid = uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid job id") from exc
    job = await repo.get_import_job(session, jid, tenant.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no import job {job_id}")
    return ImportJobOut.of(job)


async def _resolve(session: AsyncSession, instance_id: str, tenant: Tenant) -> AgentInstance:
    try:
        iid = uuid.UUID(instance_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid instance id") from exc
    instance = await repo.get_instance(session, iid, tenant.id)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no instance {instance_id}")
    return instance


async def _summary_with_name(session: AsyncSession, instance: AgentInstance) -> InstanceSummary:
    names = {t.id: t.name for t in await repo.list_templates(session)}
    return _summary(instance, names.get(instance.template_id, "?"))


def _summary(instance: AgentInstance, template_name: str) -> InstanceSummary:
    return InstanceSummary(
        instance_id=instance.id.hex,
        template=template_name,
        template_version=instance.template_version,
        status=instance.status,
        param_values=instance.param_values,
        connections={c.role: c.connection_id.hex for c in instance.connections},
        phone_number=instance.phone_number,
        created_at=instance.created_at,
    )
