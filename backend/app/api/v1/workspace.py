"""One practice's state at a glance, for an agency overview (ADR-0016) — HTTP only.

Tenant-scoped like every other route: the overview reads each workspace with a token Clerk issued
for that organization, so an agency sees exactly the workspaces it belongs to and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service
from app.api.deps import current_tenant
from app.db.session import get_session

router = APIRouter()


@router.get("/summary")
async def summary(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    return await service.workspace_summary(session, tenant.id)
