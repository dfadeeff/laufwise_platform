"""Approvals (ADR-0035) — HTTP only: what is waiting, deciding it, and sending what was approved.

Tenant-scoped like every route; another workspace's request is a 404. Every rule of the gate is
in `app.approvals.service`; a refusal there is answered with its status and reason, after any
state it recorded (an expired or withdrawn request) is committed.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, current_principal, current_tenant
from app.api.v1.workflow_agents import _case_detail, approval_view
from app.approvals import service as approvals
from app.db import repo
from app.db.session import get_session
from app.notifications.mail import Identity, transport_for

router = APIRouter()


class Decision(BaseModel):
    approve: bool
    note: str = ""


class Resolution(BaseModel):
    outcome: str  # sent | not_sent
    note: str = ""


async def _approval(session, approval_id: str, tenant):
    try:
        parsed = uuid.UUID(approval_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval") from exc
    approval = await repo.get_approval(session, parsed, tenant.id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval")
    # The case is locked while the decision is taken (red flag C3), so no save slips in between
    # the fingerprint check and the decision; `send` checks the fingerprint again before mailing.
    case = await repo.get_workflow_case(session, approval.case_id, tenant.id, lock=True)
    return approval, case


async def _send(session, case, approval, tenant) -> None:
    transport = await transport_for(session, tenant.id)
    if transport is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the platform cannot send mail")
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    sender = ((agent.settings or {}).get("sender") if agent else "") or tenant.name
    rows = (await repo.letter_deliveries(session, [approval.id], tenant.id))[approval.id]
    await approvals.send(session, case, approval, rows, transport, Identity(name=sender))


@router.get("")
async def list_pending(
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> list[dict]:
    """What is waiting for a decision in this workspace."""
    pending = []
    for approval in await repo.pending_approvals(session, tenant.id):
        case = await repo.get_workflow_case(session, approval.case_id, tenant.id)
        pending.append(
            {
                **approval_view(approval, principal, case, []),  # pending: nothing sent yet
                "case": {"id": str(case.id), "title": case.title} if case else None,
            }
        )
    return pending


@router.post("/{approval_id}/decide")
async def decide(
    approval_id: str,
    req: Decision,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Approve (and send) or reject. Approving sends the letters at once."""
    approval, case = await _approval(session, approval_id, tenant)
    try:
        approvals.decide(
            session, case, approval, principal.user_id, principal.is_admin, req.approve, req.note
        )
        await session.commit()
        if approval.status == "approved":
            await _send(session, case, approval, tenant)
            await session.commit()
    except approvals.Refused as exc:
        await session.commit()
        raise HTTPException(exc.status, str(exc)) from exc
    return await _case_detail(session, case, tenant, principal)


@router.post("/{approval_id}/send")
async def send_again(
    approval_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Send the approved letters that did not go out. One that was sent is never sent twice."""
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only a workspace administrator can send")
    approval, case = await _approval(session, approval_id, tenant)
    try:
        await _send(session, case, approval, tenant)
    except approvals.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant, principal)


@router.post("/{approval_id}/deliveries/{delivery_id}/resolve")
async def resolve(
    approval_id: str,
    delivery_id: str,
    req: Resolution,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Say what became of an uncertain letter (ADR-0043 D5): it arrived, or it did not and may
    be sent again. The platform never decides this by itself."""
    approval, case = await _approval(session, approval_id, tenant)
    try:
        delivery = await repo.get_letter_delivery(
            session, uuid.UUID(delivery_id), approval.id, tenant.id
        )
    except ValueError:
        delivery = None
    if delivery is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such delivery")
    try:
        approvals.resolve(
            session, case, delivery, principal.user_id, principal.is_admin, req.outcome, req.note
        )
    except approvals.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant, principal)
