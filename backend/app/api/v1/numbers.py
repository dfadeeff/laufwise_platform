"""Phone numbers a practice claims for itself from the platform's pool (HTTP only).

The rules — what the pool is, who may claim or release what, wiring the webhook — live in
`app.agents.numbers`. This module maps them onto requests and status codes.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import numbers
from app.api.deps import current_tenant
from app.api.v1.telephony import incoming_webhook_url
from app.db.session import get_session

router = APIRouter()


class NumberRequest(BaseModel):
    number: str


async def _view(session: AsyncSession, tenant, request: Request) -> dict:
    owned = await numbers.owned(session, tenant.id)
    try:
        available = await numbers.available(session, webhook_url=incoming_webhook_url(request))
        reason = None
    except numbers.NumberError as exc:
        available, reason = [], str(exc)
    except httpx.HTTPError:
        available, reason = [], "The phone provider could not be reached. Try again shortly."
    return {
        "owned": owned,
        "available": available,
        "max": numbers.MAX_NUMBERS_PER_PRACTICE,
        # Why nothing is offered, when nothing is: unconfigured Twilio reads differently from an
        # empty pool, and the practice should know which one it is looking at.
        "unavailable_reason": reason,
    }


@router.get("")
async def list_numbers(
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    """The practice's numbers, and the ones it could claim."""
    return await _view(session, tenant, request)


async def _change(session: AsyncSession, action) -> None:
    try:
        await action
        await session.commit()
    except numbers.NumberError as exc:
        await session.rollback()
        raise HTTPException(exc.status, str(exc)) from exc
    except httpx.HTTPError as exc:
        await session.rollback()
        raise HTTPException(503, "The phone provider could not be reached. Try again shortly.") from exc


@router.post("/claim")
async def claim_number(
    req: NumberRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    await _change(
        session,
        numbers.claim(session, tenant.id, req.number.strip(), webhook_url=incoming_webhook_url(request)),
    )
    return await _view(session, tenant, request)


@router.post("/release")
async def release_number(
    req: NumberRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    await _change(session, numbers.release(session, tenant.id, req.number.strip()))
    return await _view(session, tenant, request)
