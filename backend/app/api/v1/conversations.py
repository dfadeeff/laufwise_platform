"""Tenant-scoped read API for conversational session timelines."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.conversation import CheckOut, ConversationDetail, ConversationSummary
from app.schemas.transcript import as_markdown

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
    # What the engine checked, read from the runs this call's tools started. The agent's wording
    # and the engine's ruling belong on one screen: a call that says "you're booked" while the
    # write was blocked reads perfectly until you put them side by side.
    checks = [
        CheckOut(
            run_id=run.id.hex,
            step=str(event.payload.get("step_id", "")),
            status=str(event.payload.get("status", "")),
            reason=event.payload.get("reason"),
            expr=event.payload.get("expr"),
        )
        for run in await repo.runs_for_conversation(session, conversation, tenant_id=tenant.id)
        for event in run.events
    ]
    return ConversationDetail.of(conversation, checks)


@router.get("/{conversation_id}/export", response_class=Response)
async def export_conversation(
    conversation_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> Response:
    """One call as Markdown, to keep as an example.

    Tenant-scoped like every other read here: an id you do not own is a 404, never a leak.
    Served as an attachment so the browser saves it under the call id rather than rendering it —
    the point is a file you can put in a review or turn into an eval scenario.
    """
    try:
        parsed = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid conversation id") from exc
    conversation = await repo.get_conversation(session, parsed, tenant.id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no conversation {conversation_id}")
    return Response(
        as_markdown(conversation),
        media_type="text/markdown; charset=utf-8",
        headers={
            "content-disposition": f'attachment; filename="call-{conversation.id.hex[:8]}.md"'
        },
    )
