"""A temporary mail failure is retried; a lasting one is flagged at once (Mia spec v3 #14, #141).

The live test's summary failed with `SMTP 530 Authentication Required` — a refusal that no retry
fixes. A server that is briefly down, or answers 4xx ("try again later"), is the other kind: it
gets a couple more attempts before the call is flagged for staff.
"""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.notifications import is_temporary, send_call_summary


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BookingSession:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    monkeypatch.setattr("app.workloads.conversational.notifications.settings.smtp_host", "smtp.example.test")
    monkeypatch.setattr(
        "app.workloads.conversational.notifications.settings.smtp_from", "benachrichtigung@laufwise.test"
    )
    monkeypatch.setattr("app.workloads.conversational.notifications.RETRY_DELAYS", (0.0, 0.0))
    return BookingSession("call-retry")


def _failing(monkeypatch, *errors: Exception) -> list:
    """`deliver` raises `errors` in turn, then succeeds."""
    attempts: list = []

    def deliver(transport, message):
        attempts.append(message)
        if len(attempts) <= len(errors):
            raise errors[len(attempts) - 1]

    monkeypatch.setattr("app.workloads.conversational.notifications.mail.deliver", deliver)
    return attempts


@pytest.mark.parametrize(
    "error",
    [
        OSError("connection refused"),
        TimeoutError("timed out"),
        smtplib.SMTPServerDisconnected("gone"),
        smtplib.SMTPResponseException(421, b"Service not available, try again later"),
        smtplib.SMTPDataError(451, b"Temporary local problem"),
    ],
)
def test_a_server_that_is_down_or_says_try_later_is_temporary(error) -> None:
    assert is_temporary(error)


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPAuthenticationError(530, b"Authentication Required"),
        smtplib.SMTPAuthenticationError(535, b"Bad credentials"),
        smtplib.SMTPRecipientsRefused({"a@b.de": (550, b"No such user")}),
        smtplib.SMTPSenderRefused(553, b"Sender not allowed", "x@y.de"),
        ValueError("broken message"),
    ],
)
def test_a_refusal_is_not_temporary(error) -> None:
    assert not is_temporary(error)


def test_a_mailbox_that_is_only_temporarily_full_is_temporary() -> None:
    assert is_temporary(smtplib.SMTPRecipientsRefused({"a@b.de": (452, b"Mailbox full, try later")}))


@pytest.mark.anyio
async def test_a_temporary_failure_is_retried_until_it_goes_through(session, monkeypatch) -> None:
    attempts = _failing(monkeypatch, OSError("connection refused"), smtplib.SMTPServerDisconnected("gone"))

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery["sent"] is True
    assert delivery["attempts"] == 3
    assert len(attempts) == 3


@pytest.mark.anyio
async def test_the_live_tests_530_is_flagged_at_once_without_retrying(session, monkeypatch) -> None:
    attempts = _failing(monkeypatch, smtplib.SMTPAuthenticationError(530, b"Authentication Required"))

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery["sent"] is False
    assert delivery["attempts"] == 1 and len(attempts) == 1
    assert delivery["temporary"] is False
    assert "530" in delivery["reason"]


@pytest.mark.anyio
async def test_a_failure_that_outlasts_the_retries_is_flagged(session, monkeypatch) -> None:
    attempts = _failing(monkeypatch, *[OSError("connection refused")] * 5)

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery["sent"] is False
    assert delivery["attempts"] == 3 and len(attempts) == 3
    assert delivery["temporary"] is True
    assert "connection refused" in delivery["reason"]
