"""Runbook endpoints — list and inspect process contracts."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.control_plane.runtime import Runtime

router = APIRouter()


@router.get("")
def list_runbooks(runtime: Runtime = Depends(get_runtime)) -> list[str]:
    return runtime.list_runbooks()