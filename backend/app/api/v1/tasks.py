"""Tenant-scoped read API for operational task timelines."""

from __future__ import annotations

import uuid

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
