"""Runbook (template) endpoints — list published process contracts from the DB."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.session import get_session

router = APIRouter()


@router.get("")
async def list_runbooks(session: AsyncSession = Depends(get_session)) -> list[str]:
    return await repo.list_template_names(session)