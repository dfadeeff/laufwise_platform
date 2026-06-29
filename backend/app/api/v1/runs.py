"""Run endpoints — start a governed run, and read persisted runs from Postgres (Stage 2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_runtime
from app.control_plane.runtime import Runtime
from app.core.errors import NotFoundError, RuntimeNotConfiguredError
from app.db import repo
from app.db.models import Run
from app.db.session import get_session
from app.schemas.run import RunDetail, RunRequest, RunResult, RunSummary, StepResult

router = APIRouter()


@router.post("", response_model=RunResult)
async def start_run(
    req: RunRequest,
    runtime: Runtime = Depends(get_runtime),
    session: AsyncSession = Depends(get_session),
) -> RunResult:
    try:
        return await runtime.run(session, req)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RuntimeNotConfiguredError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc


@router.get("", response_model=list[RunSummary])
async def list_runs(session: AsyncSession = Depends(get_session)) -> list[RunSummary]:
    return [_summary(r) for r in await repo.list_runs(session)]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunDetail:
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid run id") from exc
    run = await repo.get_run(session, rid)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no run {run_id}")
    steps = [StepResult.model_validate(e.payload) for e in run.events]
    return RunDetail(**_summary(run).model_dump(), steps=steps)


def _summary(run: Run) -> RunSummary:
    return RunSummary(
        run_id=run.id.hex,
        runbook=run.template_name,
        version=run.template_version,
        status=run.status,
        started_at=run.started_at,
        trace_ref=run.trace_ref,
    )