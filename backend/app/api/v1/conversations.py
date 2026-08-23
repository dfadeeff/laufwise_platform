"""Tenant-scoped read API for conversational session timelines."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.conversation import ConversationDetail, ConversationSummary

router = APIRouter()


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[ConversationSummary]:
    return [
        ConversationSummary.of(conversation)
        for conversation in await repo.list_conversations(session, tenant.id)
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ConversationDetail:
    try:
        parsed = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid conversation id") from exc
    conversation = await repo.get_conversation(session, parsed, tenant.id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no conversation {conversation_id}")
    return ConversationDetail.of(conversation)
