"""A workspace's own state: one practice at a glance for an agency overview (ADR-0016), and the
modules it runs (ADR-0028) — HTTP only.

Tenant-scoped like every other route: the overview reads each workspace with a token Clerk issued
for that organization, so an agency sees exactly the workspaces it belongs to and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service
from app.api.deps import current_tenant, workspace_modules
from app.db import repo
from app.kernel.modules import Module, installed
from app.db.session import get_session

router = APIRouter()


@router.get("/summary")
async def summary(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    return await service.workspace_summary(session, tenant.id)


class AddModule(BaseModel):
    key: str


def _modules_view(running: list[Module]) -> dict:
    return {
        "running": [m.public() for m in running],
        "available": [m.public() for m in installed().values()],
    }


@router.get("/modules")
async def list_modules(running: list[Module] = Depends(workspace_modules)) -> dict:
    """What this workspace runs, and every module it could run."""
    return _modules_view(running)


@router.post("/modules")
async def add_module(
    req: AddModule,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    """Add a module to this workspace. There is no removal yet: a module's agents and runs would
    need somewhere to go first."""
    modules = installed()
    if req.key not in modules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no module '{req.key}'")
    await repo.add_workspace_module(session, tenant.id, req.key)
    await session.commit()
    keys = await repo.workspace_module_keys(session, tenant.id)
    return _modules_view([modules[k] for k in keys if k in modules])
