"""A letter is never sent twice, and never lost silently (ADR-0043), against the database.

What went wrong before: every outcome was written in one commit after the whole send loop, so a
crash inside it forgot letters the mail server had already taken, and "send again" mailed them a
second time. Two "send again" requests at once both sent, and a timeout after the mail server took
a letter was recorded as `failed` and offered for sending again.
"""

from __future__ import annotations

import importlib.util
import smtplib
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.approvals import service as approvals
from app.db.models import ApprovalRequest, LetterDelivery
from app.notifications import mail
from app.notifications.mail import MaybeSent, Transport
from tests.test_approvals import (
    ALICE,
    BOB,
    CAROL,
    SENT_TO,
    _as_person,
    _decide,
    _ready_case,
    _request,
    client,
    outbox,  # noqa: F401  (fixture)
)
from tests.test_workflow_agents import _PRACTICE, _as, _run_db, db
from app.api.deps import current_tenant
from app.main import app


class Crash(BaseException):
    """The process dying: no handler in the platform catches it."""


def _approved() -> tuple[dict, str]:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]
    _as_person(BOB)
    return case, approval_id


def _send_again(approval_id: str):
    return client.post(f"/api/v1/approvals/{approval_id}/send")


def _deliveries(case: dict) -> list[dict]:
    return client.get(f"/api/v1/cases/{case['id']}").json()["approval"]["deliveries"]


def _resolve(approval_id: str, delivery_id: str, outcome: str, note: str = "geprüft"):
    return client.post(
        f"/api/v1/approvals/{approval_id}/deliveries/{delivery_id}/resolve",
        json={"outcome": outcome, "note": note},
    )


def _age_sending_rows(minutes: int) -> None:
    async def go(s):
        await s.execute(
            update(LetterDelivery)
            .where(LetterDelivery.status == "sending")
            .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
        )
        await s.commit()

    _run_db(go)


@db
def test_a_crash_while_sending_never_sends_an_accepted_letter_again(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()
    real = approvals.deliver

    def dies_on_the_second(transport, message):
        if message["To"] == "berg@example.org":
            raise Crash()
        real(transport, message)

    monkeypatch.setattr(approvals, "deliver", dies_on_the_second)
    with pytest.raises(Crash):
        _decide(approval_id, True)

    monkeypatch.setattr(approvals, "deliver", real)
    _send_again(approval_id)

    # Adler's was taken before the crash: never again. Berg's may have gone out: not resent.
    # Clausen's was never handed over: sent now, once.
    assert Counter(m["To"] for m in outbox) == {"adler@example.org": 1, "clausen@example.org": 1}
    assert [(d["recipient"], d["status"]) for d in _deliveries(case)] == [
        ("Herr Adler", "sent"),
        ("Frau Berg", "sending"),
        ("Firma Clausen", "sent"),
    ]


@db
def test_a_letter_left_sending_by_a_dead_process_becomes_uncertain(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()

    def dies(transport, message):
        raise Crash()

    monkeypatch.setattr(approvals, "deliver", dies)
    with pytest.raises(Crash):
        _decide(approval_id, True)
    _age_sending_rows(6)

    assert [d["status"] for d in _deliveries(case)][0] == "uncertain"
    monkeypatch.setattr(approvals, "deliver", lambda t, m: outbox.append(m))
    after = _send_again(approval_id).json()["approval"]["deliveries"]
    assert after[0]["status"] == "uncertain"  # written now, and not resent
    assert "adler@example.org" not in [m["To"] for m in outbox]


@db
def test_two_sends_at_once_hand_every_letter_to_the_mail_server_once(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()
    outbox.refuse.update(SENT_TO)
    _decide(approval_id, True)  # all three refused: all three may be sent again
    outbox.refuse.clear()
    lock = threading.Lock()

    def slow(transport, message):
        time.sleep(0.3)
        with lock:
            outbox.append(message)

    monkeypatch.setattr(approvals, "deliver", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: _send_again(approval_id), range(2)))

    assert Counter(m["To"] for m in outbox) == {address: 1 for address in SENT_TO}
    assert [d["status"] for d in _deliveries(case)] == ["sent", "sent", "sent"]


@db
def test_a_timeout_while_handing_over_is_uncertain_and_not_sent_again(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()
    real = approvals.deliver

    def times_out_for_berg(transport, message):
        if message["To"] == "berg@example.org":
            raise MaybeSent("timed out")
        real(transport, message)

    monkeypatch.setattr(approvals, "deliver", times_out_for_berg)
    first = _decide(approval_id, True).json()["approval"]["deliveries"]
    assert [d["status"] for d in first] == ["sent", "uncertain", "sent"]

    monkeypatch.setattr(approvals, "deliver", real)
    _send_again(approval_id)
    assert "berg@example.org" not in [m["To"] for m in outbox]


@db
def test_every_letter_carries_the_message_id_of_its_delivery(outbox) -> None:  # noqa: F811
    case, approval_id = _approved()
    deliveries = _decide(approval_id, True).json()["approval"]["deliveries"]

    assert [m["Message-ID"] for m in outbox] == [
        f"<{d['id']}@laufwise.test>" for d in deliveries
    ]


@db
def test_an_administrator_resolves_an_uncertain_letter_with_a_reason(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()
    real = approvals.deliver

    def uncertain_for_adler_and_berg(transport, message):
        if message["To"] in ("adler@example.org", "berg@example.org"):
            raise MaybeSent("connection dropped")
        real(transport, message)

    monkeypatch.setattr(approvals, "deliver", uncertain_for_adler_and_berg)
    adler, berg, _ = _decide(approval_id, True).json()["approval"]["deliveries"]
    monkeypatch.setattr(approvals, "deliver", real)

    assert _resolve(approval_id, adler["id"], "sent", note="").status_code == 422
    assert _resolve(approval_id, adler["id"], "maybe").status_code == 422
    _as_person(CAROL)
    assert _resolve(approval_id, adler["id"], "sent").status_code == 403
    _as_person(ALICE)  # an administrator, also the requester: resolving is not deciding
    assert _resolve(approval_id, adler["id"], "sent", "Im Gesendet-Ordner").status_code == 200
    assert _resolve(approval_id, berg["id"], "not_sent", "Frau Berg hat nichts erhalten").status_code == 200
    assert _resolve(approval_id, berg["id"], "sent").status_code == 409  # no longer uncertain

    resolved = _deliveries(case)
    assert [(d["status"], d["note"]) for d in resolved[:2]] == [
        ("sent", "Im Gesendet-Ordner"),
        ("not_sent", "Frau Berg hat nichts erhalten"),
    ]
    sent_before = len(outbox)
    _send_again(approval_id)
    _send_again(approval_id)
    assert [m["To"] for m in outbox[sent_before:]] == ["berg@example.org"]  # once, not twice
    kinds = [e["kind"] for e in client.get(f"/api/v1/cases/{case['id']}").json()["events"]]
    assert kinds.count("delivery_resolved") == 2


@db
def test_another_workspace_cannot_resolve_a_delivery(outbox, monkeypatch) -> None:  # noqa: F811
    case, approval_id = _approved()
    monkeypatch.setattr(approvals, "deliver", lambda t, m: (_ for _ in ()).throw(MaybeSent("x")))
    delivery = _decide(approval_id, True).json()["approval"]["deliveries"][0]

    app.dependency_overrides[current_tenant] = _as(_PRACTICE, "praxis")
    assert _resolve(approval_id, delivery["id"], "sent").status_code == 404


@db
def test_the_database_refuses_a_second_live_delivery_of_one_letter(outbox) -> None:  # noqa: F811
    case, approval_id = _approved()
    _decide(approval_id, True)

    async def second(s):
        approval = await s.get(ApprovalRequest, uuid.UUID(approval_id))
        s.add(
            LetterDelivery(
                tenant_id=approval.tenant_id,
                approval_id=approval.id,
                recipient="Herr Adler",
                email="adler@example.org",
                title="again",
                status="sending",
            )
        )
        await s.commit()

    with pytest.raises(IntegrityError):
        _run_db(second)


@db
def test_the_migration_copies_the_deliveries_recorded_before_it(outbox) -> None:  # noqa: F811
    case, approval_id = _approved()
    old = [
        {"recipient": "Herr Adler", "email": "adler@example.org", "title": "T", "status": "sent",
         "at": "2026-11-01T10:00:00+00:00"},
        {"recipient": "Frau Berg", "email": "berg@example.org", "title": "T", "status": "failed",
         "error": "550 mailbox unavailable", "at": "2026-11-01T10:00:01+00:00"},
    ]
    path = next(Path(__file__).parents[1].glob("migrations/versions/*_letter_delivery.py"))
    spec = importlib.util.spec_from_file_location("letter_delivery_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def go(s):
        await s.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == uuid.UUID(approval_id))
            .values(status="approved", deliveries=old)
        )
        await s.execute(text(migration.BACKFILL))
        await s.execute(text(migration.BACKFILL))  # twice: copies nothing the second time
        await s.commit()
        rows = await s.execute(
            select(LetterDelivery.recipient, LetterDelivery.status, LetterDelivery.error)
            .where(LetterDelivery.approval_id == uuid.UUID(approval_id))
            .order_by(LetterDelivery.created_at)
        )
        return rows.all()

    assert _run_db(go) == [
        ("Herr Adler", "sent", None),
        ("Frau Berg", "failed", "550 mailbox unavailable"),
    ]


# The mail server's answer decides `failed` or `uncertain` (D3) — no database needed.


class FakeSMTP:
    """A mail server whose `send_message` and `quit` do what the test says."""

    on_send: BaseException | None = None
    on_quit: BaseException | None = None
    sent: list = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):  # as smtplib's: leaving the block says QUIT
        self.quit()

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        if self.on_send is not None:
            raise self.on_send
        FakeSMTP.sent.append(message)

    def quit(self):
        if self.on_quit is not None:
            raise self.on_quit

    def close(self):
        pass


def _deliver_with(monkeypatch, on_send=None, on_quit=None) -> None:
    FakeSMTP.on_send, FakeSMTP.on_quit, FakeSMTP.sent = on_send, on_quit, []
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.deliver(Transport("smtp.test", 587, "noreply@laufwise.test"), EmailMessage())


@pytest.mark.parametrize(
    "refusal",
    [
        smtplib.SMTPRecipientsRefused({"a@b.de": (550, b"no such user")}),
        smtplib.SMTPSenderRefused(553, b"not allowed", "noreply@laufwise.test"),
        smtplib.SMTPDataError(554, b"rejected as spam"),
    ],
)
def test_a_refusal_with_an_smtp_code_means_nothing_was_sent(monkeypatch, refusal) -> None:
    with pytest.raises(smtplib.SMTPException) as raised:
        _deliver_with(monkeypatch, on_send=refusal)
    assert not isinstance(raised.value, MaybeSent)


@pytest.mark.parametrize(
    "lost", [smtplib.SMTPServerDisconnected("gone"), TimeoutError("timed out"), OSError("reset")]
)
def test_a_connection_lost_while_handing_over_may_have_sent(monkeypatch, lost) -> None:
    with pytest.raises(MaybeSent):
        _deliver_with(monkeypatch, on_send=lost)


def test_a_failure_after_the_server_took_the_message_is_still_sent(monkeypatch) -> None:
    _deliver_with(monkeypatch, on_quit=OSError("reset while saying goodbye"))
    assert len(FakeSMTP.sent) == 1
