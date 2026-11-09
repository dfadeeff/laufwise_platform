"""Requesting, deciding and executing an approval for a case's letters (ADR-0035) — no FastAPI.

The gate's rules, all here and all enforced on the server:

- Only a ready case with letters can be put up for approval, and only while mail can be sent;
  so can a case waiting for a fact (ADR-0039 step 3b), whose letters say what already stands.
- What is approved is fixed when it is requested: a fingerprint of the input revision, the
  result, every letter and every address. If any of it changes, the request is withdrawn.
- The person who requested it never decides it, and only a workspace administrator decides.
  A rejection needs a reason. A request expires after seven days.
- Approving re-checks the fingerprint against the case as it is now (precondition), then sends
  each letter and records what the mail server said for each one (postcondition, per letter).
  A letter that failed can be sent again; one that was sent is never sent twice.
- A letter is never sent twice, and never lost silently (ADR-0043): each attempt is a
  `letter_delivery` row committed as `sending` before the mail server is contacted, and the
  database allows one live row per letter. When the platform cannot know whether a letter went
  out, it is `uncertain` until an administrator resolves it with a reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import (
    LIVE_DELIVERY,
    ApprovalRequest,
    LetterDelivery,
    WorkflowCase,
    WorkflowCaseEvent,
)
from app.kernel.workflows import Draft
from app.letters.pdf import letter_pdf, today
from app.notifications.mail import Identity, MaybeSent, Transport, compose, deliver

EXPIRY = timedelta(days=7)
# The case statuses whose letters stand: ready, and waiting for a fact the rest does not need.
SENDABLE = ("ready", "waiting")
SEND_LETTERS = "send_letters"
# A `sending` row this old belongs to a process that died (ADR-0043 D4). The SMTP timeout of 20 s
# is per socket operation, and a send makes about fifteen of them, so a slow server can take
# minutes; thirty is far beyond that. `updated_at` is the database's clock and this the app's;
# their skew is seconds.
STALE = timedelta(minutes=30)
RESOLUTIONS = ("sent", "not_sent")
INTERRUPTED = "Der Versand wurde unterbrochen; ob der Brief ankam, ist unbekannt."
ONE_LIVE = "uq_letter_delivery_one_live"  # the index that allows one live delivery per letter
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


def _as_draft(stored: dict) -> Draft:
    """A draft as the case stores it (a dict), back as the kernel's `Draft`."""
    return Draft(**{k: v for k, v in stored.items() if k in Draft.__dataclass_fields__})


def pdf_name(draft: dict) -> str:
    """A file name a mail client and a file system accept: the title and the recipient."""
    name = f"{draft['title']} – {draft['recipient']}"
    return "".join(c for c in name if c not in '/\\:*?"<>|').strip() + ".pdf"


def letter_as_pdf(draft: dict, on: date) -> bytes:
    return letter_pdf(_as_draft(draft), on)


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


def status_of(delivery: LetterDelivery, now: datetime | None = None) -> str:
    """A delivery's status as it stands: one left `sending` by a dead process is uncertain."""
    if delivery.status == "sending" and delivery.updated_at < (now or _now()) - STALE:
        return "uncertain"
    return delivery.status


def latest(approval: ApprovalRequest, rows: list[LetterDelivery]) -> list[LetterDelivery]:
    """Each approved letter's latest attempt, in the approval's order; a letter never tried has
    none."""
    last = {row.recipient: row for row in rows}  # rows are oldest first
    return [
        last[letter["recipient"]]
        for letter in approval.payload.get("letters", [])
        if letter["recipient"] in last
    ]


def sent_letters(
    case: WorkflowCase,
    approvals: list[ApprovalRequest],
    deliveries: dict[uuid.UUID, list[LetterDelivery]],
) -> list[dict]:
    """The case's letters, as it shows them now, that were sent: the approval that sent them
    fixed exactly these letters (its fingerprint still matches), and the mail server took each."""
    sent = {
        (d.recipient, d.title)
        for a in approvals
        if a.status == "approved" and a.fingerprint == fingerprint(case, a.payload)
        for d in deliveries.get(a.id, [])
        if d.status == "sent"
    }
    return [d for d in case.drafts if (d["recipient"], d["title"]) in sent]


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
    if case.status not in SENDABLE or not case.drafts:
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
    rows: list[LetterDelivery],
    transport: Transport,
    identity: Identity,
) -> None:
    """Send every approved letter that has no live delivery, and record each outcome.

    `rows` are the approval's deliveries as just read. Each letter's intent is committed before
    the mail server is contacted and its outcome as soon as it is known, so a crash at any point
    leaves a record. Another request that writes the same letter's intent first wins: the
    database refuses the second, and this request sends nothing for it.
    """
    if approval.status != "approved":
        raise Refused(409, "only approved letters are sent")
    if fingerprint(case, approval.payload) != approval.fingerprint:
        raise Refused(409, "the case changed since it was approved; nothing was sent")
    now = _now()
    for row in rows:
        if status_of(row, now) == "uncertain" and row.status == "sending":
            if await repo.update_delivery_if(
                session, row.id, "sending", status="uncertain", error=INTERRUPTED
            ):
                row.status, row.error = "uncertain", INTERRUPTED
    live = {row.recipient for row in rows if row.status in LIVE_DELIVERY}
    drafts = {d["recipient"]: d for d in case.drafts}
    outcomes: list[str] = []
    for letter in approval.payload["letters"]:
        if letter["recipient"] in live:
            continue
        delivery_id = uuid.uuid4()
        # Built before the intent is written: a letter that cannot be rendered is never left
        # `sending`, since it was never handed over.
        message = _message(drafts[letter["recipient"]], letter, transport, identity, delivery_id)
        delivery = LetterDelivery(
            id=delivery_id,
            tenant_id=approval.tenant_id,
            approval_id=approval.id,
            recipient=letter["recipient"],
            email=letter["email"],
            title=letter["title"],
            status="sending",
            note="",
        )
        try:
            async with session.begin_nested():
                session.add(delivery)
        except IntegrityError as exc:
            if ONE_LIVE not in str(exc.orig):
                raise
            continue  # another request is sending this letter
        await session.commit()  # the intent, before the mail server is contacted
        try:
            await asyncio.to_thread(deliver, transport, message)
        except MaybeSent as exc:  # it may have gone out: never offered for an ordinary retry
            status, error = "uncertain", str(exc)[:300]
        except Exception as exc:  # the mail server's refusal, for the person who retries
            status, error = "failed", str(exc)[:300]
        else:
            status, error = "sent", None
        # Only while it is still `sending`: if it looked dead and was marked uncertain or
        # resolved meanwhile, that stands, and the late answer goes into the history.
        if not await repo.update_delivery_if(
            session, delivery_id, "sending", status=status, error=error
        ):
            _event(
                session, case, "late_delivery_outcome", recipient=letter["recipient"], outcome=status
            )
        await session.commit()
        outcomes.append(status)
    if outcomes:
        _event(
            session,
            case,
            "letters_sent",
            sent=outcomes.count("sent"),
            failed=outcomes.count("failed"),
            uncertain=outcomes.count("uncertain"),
        )


def _message(
    draft: dict, letter: dict, transport: Transport, identity: Identity, delivery_id: uuid.UUID
):
    message = compose(
        transport=transport,
        identity=identity,
        to=[letter["email"]],
        subject=draft["title"],
        body=draft["text"],
    )
    # Found again in a sent folder or the provider's log by this id (ADR-0043 D6).
    message["Message-ID"] = f"<{delivery_id}@{transport.from_address.rpartition('@')[2]}>"
    # The letter and its enclosure as one PDF, dated the day it is sent (ADR-0037).
    message.add_attachment(
        letter_pdf(_as_draft(draft), today()),
        maintype="application",
        subtype="pdf",
        filename=pdf_name(draft),
    )
    return message


def ensure_settled(deliveries: dict[uuid.UUID, list[LetterDelivery]]) -> None:
    """Refuse to change a case while one of its letters is being sent or may have gone out: the
    approval would no longer describe the case, and its letter would be offered again (ADR-0043
    D8). An administrator resolving the letter ends it."""
    if any(status_of(d) in ("sending", "uncertain") for rows in deliveries.values() for d in rows):
        raise Refused(
            409,
            "a letter of this case is being sent or may have gone out; resolve it before "
            "changing the case",
        )


async def resolve(
    session: AsyncSession,
    case: WorkflowCase,
    delivery: LetterDelivery,
    resolved_by: str,
    is_admin: bool,
    outcome: str,
    note: str,
) -> None:
    """An administrator says what became of an uncertain letter, and why they know (D5)."""
    if not is_admin:
        raise Refused(403, "only a workspace administrator can resolve a delivery")
    if outcome not in RESOLUTIONS:
        raise Refused(422, "a delivery is resolved as sent or not_sent")
    if not note.strip():
        raise Refused(422, "say how you know")
    if status_of(delivery) != "uncertain":
        raise Refused(409, f"this delivery is {status_of(delivery)}, not uncertain")
    # Only from the status just read: a request that answers late may have settled it meanwhile.
    if not await repo.update_delivery_if(
        session,
        delivery.id,
        delivery.status,
        status=outcome,
        resolved_by=resolved_by,
        resolved_at=_now(),
        note=note.strip(),
    ):
        raise Refused(409, "this delivery changed meanwhile; look at it again")
    _event(
        session,
        case,
        "delivery_resolved",
        recipient=delivery.recipient,
        outcome=outcome,
        by=resolved_by,
    )
