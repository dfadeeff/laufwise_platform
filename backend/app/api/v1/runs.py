"""Run endpoints — start a governed run of a runbook against a case."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_runtime
from app.control_plane.runtime import Runtime
from app.core.errors import RuntimeNotConfiguredError
from app.schemas.run import RunRequest, RunResult

router = APIRouter()


@router.post("", response_model=RunResult)
def start_run(req: RunRequest, runtime: Runtime = Depends(get_runtime)) -> RunResult:
    try:
        return runtime.run(req)
    except RuntimeNotConfiguredError as exc:
        # The engine isn't wired yet — surface it honestly, don't fake a result.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc