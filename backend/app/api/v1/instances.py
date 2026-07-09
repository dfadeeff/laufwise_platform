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
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import AgentInstance, Tenant
from app.db.session import get_session
from app.instances.deploy import validate_param_values
from app.schemas.connection import ImportReportOut
from app.schemas.instance import DeployRequest, InstanceRunRequest, InstanceSummary
from app.schemas.run import RunResult
from app.sync.orchestrator import run_import
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


@router.post("/{instance_id}/import", response_model=ImportReportOut)
async def import_appointments(
    instance_id: str,
    runtime: Runtime = Depends(get_runtime),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ImportReportOut:
    """Run a governed calendar import (ADR-0004): enumerate the source and append each appointment
    into the destination, idempotently and verified. Returns the completeness report."""
    instance = await _resolve(session, instance_id, tenant)
    if instance.status != "deployed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"instance is {instance.status}, not deployed"
        )
    window = {
        "from": instance.param_values.get("window_from"),
        "to": instance.param_values.get("window_to"),
    }
    report = await run_import(session, runtime, instance, window)
    return ImportReportOut(
        total=report.total,
        created=report.created,
        skipped=report.skipped,
        failed=report.failed,
        excluded=report.excluded,
        complete=report.complete,
    )


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