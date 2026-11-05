"""A workspace's schedules (ADR-0040 D8) — HTTP only; the rules live in `app.schedules`.

Administrators create, change, resume, delete and run a schedule now. Any member reads them and
may pause one: stopping is always safe. Another workspace's schedule or agent is a 404.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, current_principal, current_tenant
from app.db import repo
from app.db.models import AgentSchedule
from app.db.session import get_session
from app.schedules import rules, service
from app.schedules.targets import NotRunnable, TargetNotFound

router = APIRouter(dependencies=[Depends(current_tenant)])

# An input is typed or pasted in the Studio: bounded like a case's input (workflow_agents.py).
_MAX_INPUT_BYTES = 256_000


def _bounded(value: dict) -> dict:
    if len(json.dumps(value, default=str)) > _MAX_INPUT_BYTES:
        raise ValueError(f"larger than {_MAX_INPUT_BYTES // 1000} kB")
    return value


class TargetRef(BaseModel):
    kind: str = Field(max_length=20)
    id: str


class NewSchedule(BaseModel):
    target: TargetRef
    version_id: str | None = None  # absent: the newest published version
    name: str = Field(min_length=1, max_length=120)
    rule: dict
    timezone: str | None = None  # absent: Europe/Berlin
    input: dict = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("input")
    @classmethod
    def _size(cls, value: dict) -> dict:
        return _bounded(value)


class ScheduleChange(BaseModel):
    revision: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rule: dict | None = None
    timezone: str | None = None
    input: dict | None = None
    version_id: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("input")
    @classmethod
    def _size(cls, value: dict | None) -> dict | None:
        return value if value is None else _bounded(value)


class Preview(BaseModel):
    rule: dict
    timezone: str | None = None
    count: int = Field(default=5, ge=1, le=10)


def _uuid(value: str, what: str, code: int = status.HTTP_404_NOT_FOUND) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(code, f"no such {what}") from exc


def _admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an administrator of this workspace does this")


async def _schedule(session: AsyncSession, schedule_id: str, tenant, *, lock: bool = False) -> AgentSchedule:
    schedule = await repo.get_schedule(session, _uuid(schedule_id, "schedule"), tenant.id, lock=lock)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such schedule")
    return schedule


_REFUSALS = (
    NotRunnable, rules.RuleError, service.StaleRevision, service.LimitReached, service.AlreadyQueued
)


def _refused(exc: Exception) -> HTTPException:
    """The HTTP answer to one of `_REFUSALS`."""
    if isinstance(exc, TargetNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "no such agent")
    if isinstance(exc, (service.StaleRevision, service.LimitReached, service.AlreadyQueued)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))


@router.post("/preview")
async def preview(req: Preview) -> dict[str, Any]:
    try:
        return {"times": service.preview(req.rule, req.timezone, req.count)}
    except rules.RuleError as exc:
        raise _refused(exc) from exc


@router.get("/status")
async def scheduler_status(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Whether the platform's schedule worker is running (no workspace data in it)."""
    return await service.worker_status(session)


@router.get("/targets/{kind}/{target_id}")
async def target_schedulability(
    kind: str, target_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict[str, Any]:
    """Whether this agent or deployment can be scheduled, why not, and with which input."""
    try:
        return await service.schedulability(session, tenant.id, kind, _uuid(target_id, "agent"))
    except TargetNotFound as exc:
        raise _refused(exc) from exc


@router.get("")
async def list_schedules(
    target_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> list[dict[str, Any]]:
    target = _uuid(target_id, "agent") if target_id else None
    return [await service.view(session, s) for s in await repo.list_schedules(session, tenant.id, target)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    req: NewSchedule,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _admin(principal)
    try:
        schedule = await service.create(
            session, tenant.id, principal.user_id,
            kind=req.target.kind, target_id=_uuid(req.target.id, "agent"),
            version_id=_uuid(req.version_id, "version", 422) if req.version_id else None,
            name=req.name, rule=req.rule, tz=req.timezone, input=req.input,
            enabled=req.enabled,
        )
    except _REFUSALS as exc:
        raise _refused(exc) from exc
    await session.commit()
    return await service.view(session, schedule)


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict[str, Any]:
    return await service.view(session, await _schedule(session, schedule_id, tenant))


@router.patch("/{schedule_id}")
async def change_schedule(
    schedule_id: str,
    req: ScheduleChange,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    schedule = await _schedule(session, schedule_id, tenant, lock=True)
    _admin(principal)
    changes = req.model_dump(exclude_unset=True, exclude={"revision"})
    if "version_id" in changes and changes["version_id"] is not None:
        changes["version_id"] = _uuid(changes["version_id"], "version", 422)
    changes = {k: v for k, v in changes.items() if v is not None}
    try:
        await service.update(session, schedule, principal.user_id, req.revision, changes)
    except _REFUSALS as exc:
        raise _refused(exc) from exc
    await session.commit()
    return await service.view(session, schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> Response:
    schedule = await _schedule(session, schedule_id, tenant, lock=True)
    _admin(principal)
    await service.delete(session, schedule, principal.user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{schedule_id}/pause")
async def pause_schedule(
    schedule_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    schedule = await _schedule(session, schedule_id, tenant, lock=True)
    await service.pause(session, schedule, principal.user_id)  # any member: stopping is safe
    await session.commit()
    return await service.view(session, schedule)


@router.post("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    schedule = await _schedule(session, schedule_id, tenant, lock=True)
    _admin(principal)
    try:
        await service.resume(session, schedule, principal.user_id)
    except _REFUSALS as exc:
        raise _refused(exc) from exc
    await session.commit()
    return await service.view(session, schedule)


@router.post("/{schedule_id}/run-now", status_code=status.HTTP_202_ACCEPTED)
async def run_schedule_now(
    schedule_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    schedule = await _schedule(session, schedule_id, tenant)
    _admin(principal)
    try:
        occurrence = await service.run_now(session, schedule)
    except _REFUSALS as exc:
        raise _refused(exc) from exc
    await session.commit()
    return service.occurrence_view(occurrence)


@router.get("/{schedule_id}/occurrences")
async def schedule_occurrences(
    schedule_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> list[dict[str, Any]]:
    schedule = await _schedule(session, schedule_id, tenant)
    rows = await repo.schedule_occurrences(session, schedule.id, tenant.id, limit)
    return [service.occurrence_view(o) for o in rows]
