"""Requesting, deciding and executing an approval for a case's letters (ADR-0034) — no FastAPI.

The gate's rules, all here and all enforced on the server:

- Only a ready case with letters can be put up for approval, and only while mail can be sent.
- What is approved is fixed when it is requested: a fingerprint of the input revision, the
  result, every letter and every address. If any of it changes, the request is withdrawn.
- The person who requested it never decides it, and only a workspace administrator decides.
  A rejection needs a reason. A request expires after seven days.
- Approving re-checks the fingerprint against the case as it is now (precondition), then sends
  each letter and records what the mail server said for each one (postcondition, per letter).
  A letter that failed can be sent again; one that was sent is never sent twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApprovalRequest, WorkflowCase, WorkflowCaseEvent
from app.notifications.mail import Identity, Transport, compose, deliver

EXPIRY = timedelta(days=7)
SEND_LETTERS = "send_letters"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Refused(Exception):
    """The gate said no; `status` is the HTTP status that says why. Any state it changed (an
    expired or withdrawn request) is still to be committed."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(session: AsyncSession, case: WorkflowCase, kind: str, **payload) -> None:
    session.add(WorkflowCaseEvent(case_id=case.id, kind=kind, payload=payload))


def fingerprint(case: WorkflowCase, payload: dict) -> str:
    """What exactly is approved, as one hash: change any letter, address or figure, and it differs."""
    material = {
        "revision": case.input_revision,
        "result": case.result,
        "drafts": case.drafts,
        "payload": payload,
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def withdraw_pending(
    session: AsyncSession, case: WorkflowCase, approvals: list[ApprovalRequest]
) -> None:
    """The case is about to change: whatever was waiting for approval no longer describes it."""
    for approval in approvals:
        if approval.status == "pending":
            approval.status = "withdrawn"
            approval.note = "Der Fall wurde nach der Anfrage geändert."
            _event(session, case, "approval_withdrawn")


def request(
    session: AsyncSession,
    case: WorkflowCase,
    approvals: list[ApprovalRequest],
    requested_by: str,
    emails: dict[str, str],
) -> ApprovalRequest:
    if case.status != "ready" or not case.drafts:
        raise Refused(409, "only a ready case with letters can be put up for approval")
    if any(a.status == "pending" for a in approvals):
        raise Refused(409, "this case is already waiting for approval")
    letters = []
    for draft in case.drafts:
        email = (emails.get(draft["recipient"]) or "").strip()
        if not _EMAIL.match(email):
            raise Refused(422, f"no valid email address for {draft['recipient']}")
        letters.append({"recipient": draft["recipient"], "email": email, "title": draft["title"]})
    payload = {"letters": letters}
    # A corrected settlement is sent again after its own approval; the same letters never are.
    if any(
        a.status == "approved" and a.fingerprint == fingerprint(case, payload) for a in approvals
    ):
        raise Refused(409, "these exact letters were already approved")
    approval = ApprovalRequest(
        tenant_id=case.tenant_id,
        case_id=case.id,
        action=SEND_LETTERS,
        status="pending",
        fingerprint=fingerprint(case, payload),
        payload=payload,
        requested_by=requested_by,
        expires_at=_now() + EXPIRY,
        note="",
        deliveries=[],
    )
    session.add(approval)
    _event(
        session,
        case,
        "approval_requested",
        by=requested_by,
        recipients=[letter["recipient"] for letter in letters],
    )
    return approval


def decide(
    session: AsyncSession,
    case: WorkflowCase,
    approval: ApprovalRequest,
    decided_by: str,
    is_admin: bool,
    approve: bool,
    note: str,
) -> None:
    if approval.status != "pending":
        raise Refused(409, f"this request is {approval.status}, not waiting for a decision")
    if _now() > approval.expires_at:
        approval.status = "expired"
        _event(session, case, "approval_expired")
        raise Refused(409, "this request expired; request approval again")
    if decided_by == approval.requested_by:
        raise Refused(403, "you requested this approval; another administrator must decide it")
    if not is_admin:
        raise Refused(403, "only a workspace administrator can decide an approval")
    if not approve and not note.strip():
        raise Refused(422, "say why it is rejected")
    if approve and fingerprint(case, approval.payload) != approval.fingerprint:
        approval.status = "withdrawn"
        approval.note = "Der Fall wurde nach der Anfrage geändert."
        _event(session, case, "approval_withdrawn")
        raise Refused(409, "the case changed since approval was requested; request it again")
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = decided_by
    approval.decided_at = _now()
    approval.note = note.strip()
    _event(session, case, "approval_decided", by=decided_by, status=approval.status)


async def send(
    session: AsyncSession,
    case: WorkflowCase,
    approval: ApprovalRequest,
    transport: Transport,
    identity: Identity,
) -> None:
    """Send every approved letter not yet sent, and record each outcome."""
    if approval.status != "approved":
        raise Refused(409, "only approved letters are sent")
    if fingerprint(case, approval.payload) != approval.fingerprint:
        raise Refused(409, "the case changed since it was approved; nothing was sent")
    drafts = {d["recipient"]: d for d in case.drafts}
    sent = {d["recipient"] for d in approval.deliveries if d["status"] == "sent"}
    deliveries = [d for d in approval.deliveries if d["recipient"] in sent]
    for letter in approval.payload["letters"]:
        if letter["recipient"] in sent:
            continue
        draft = drafts[letter["recipient"]]
        message = compose(
            transport=transport,
            identity=identity,
            to=[letter["email"]],
            subject=draft["title"],
            body=draft["text"],
        )
        if draft.get("enclosure"):
            message.add_attachment(
                draft["enclosure"], subtype="plain", filename=draft["enclosure_name"]
            )
        try:
            await asyncio.to_thread(deliver, transport, message)
        except Exception as exc:  # the mail server's refusal, recorded for the person who retries
            deliveries.append(
                {**letter, "status": "failed", "error": str(exc)[:300], "at": _now().isoformat()}
            )
        else:
            deliveries.append({**letter, "status": "sent", "at": _now().isoformat()})
    approval.deliveries = deliveries
    _event(
        session,
        case,
        "letters_sent",
        sent=sum(d["status"] == "sent" for d in deliveries),
        failed=sum(d["status"] == "failed" for d in deliveries),
    )
