"""The workspace's website price list (ADR-0032, Mia spec #6) — HTTP only.

The practice names its price page once; the platform reads it hourly and on demand. What the page
showed, and when, is what calls quote: a service is linked to a row in the service catalogue.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant, requires_surface
from app.db import repo
from app.db.session import get_session
from app.prices.sync import refresh

router = APIRouter(dependencies=[Depends(current_tenant)])


class PricePage(BaseModel):
    url: str = Field(min_length=8, max_length=500, pattern=r"^https?://")


def _summary(row) -> dict:
    if row is None:
        return {"url": "", "rows": [], "checked_at": None, "attempted_at": None, "error": ""}
    return {
        "url": row.url,
        # A row the last successful read did not find is shown as such, with its last price.
        "rows": [
            {**r, "on_page": bool(row.checked_at) and r["seen_at"] == row.checked_at.isoformat()}
            for r in row.rows or []
        ],
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "attempted_at": row.attempted_at.isoformat() if row.attempted_at else None,
        "error": row.error or "",
    }


@router.get("")
async def get_prices(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    return _summary(await repo.get_price_list(session, tenant.id))


@router.put("", dependencies=[requires_surface("voice_agents")])
async def set_price_page(
    req: PricePage, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    """Name the price page, and read it straight away."""
    row = await repo.set_price_list_url(session, tenant.id, req.url.strip())
    await refresh(session, row)
    return _summary(row)


@router.post("/refresh", dependencies=[requires_surface("voice_agents")])
async def refresh_prices(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    row = await repo.get_price_list(session, tenant.id)
    if row is not None:
        await refresh(session, row)
    return _summary(row)
