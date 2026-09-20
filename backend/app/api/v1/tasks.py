"""Tenant-scoped read API for operational task timelines."""

from __future__ import annotations

import uuid
from typing import Literal
from fastapi import Header
from pydantic import BaseModel
from app.auth.clerk import verify_token


from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.task import TaskDetail, TaskSummary

router = APIRouter()


@router.get("", response_model=list[TaskSummary])
async def list_tasks(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[TaskSummary]:
    return [TaskSummary.of(task) for task in await repo.list_tasks(session, tenant.id)]


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> TaskDetail:
    try:
        parsed = uuid.UUID(task_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid task id") from exc
    task = await repo.get_task(session, parsed, tenant.id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no task {task_id}")
    return TaskDetail.of(task)


class FollowupAction(BaseModel):
    action: Literal["claim", "complete"]


@router.post("/{task_id}/followup", response_model=TaskSummary)
async def update_followup(
    task_id: str,
    req: FollowupAction,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
):
    # current_tenant already verified this session; the subject supplies audit attribution.
    actor = (
        verify_token(authorization.split(" ", 1)[1]).user_id if authorization else "local-operator"
    )
    try:
        task = await repo.resolve_call_followup(
            session, uuid.UUID(task_id), tenant.id, actor=actor, complete=req.action == "complete"
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if task is None:
        raise HTTPException(404, "Callback not found")
    return TaskSummary.of(task)
