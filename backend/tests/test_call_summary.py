"""Every call produces a summary, it goes to both mailboxes, and it says less than we know.

Spec §3.9 makes the summary unconditional and §7 makes its contents restricted. Both are tested
here, because both are the kind of requirement that decays silently: a missing email is invisible
to everyone except the practice, and a date of birth that creeps into an ordinary mailbox is
invisible until it matters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.connectors.base import Appointment
from app.workloads.conversational.booking import (
    OUTCOME_BOOKED,
    OUTCOME_CALLBACK,
    OUTCOME_ERROR,
    OUTCOME_INFO,
    BookingSession,
)
from app.workloads.conversational.notifications import body_for, send_call_summary, subject_for
from app.workloads.conversational.practice import load_practice


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BookingSession:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    return BookingSession("call-abc123")


def test_the_subject_carries_the_action_the_name_and_the_time(session: BookingSession) -> None:
    """`[Voice Agent] {ACTION} — {patient_name_or_unknown} — {call_date_time}` (spec §3.9)."""
    session.set_details(first_name="Anna", last_name="Weber")
    summary = session.summary(now=datetime(2026, 9, 7, 10, 30))

    assert subject_for(summary) == "[Voice Agent] NICHT ABGESCHLOSSEN — Anna Weber — 2026-09-07 10:30"


def test_an_anonymous_caller_is_named_unknown_rather_than_left_blank(
    session: BookingSession,
) -> None:
    session.caller_turns = 3
    summary = session.summary(now=datetime(2026, 9, 7, 10, 30))

    assert summary["outcome"] == OUTCOME_INFO
    assert "unbekannt" in subject_for(summary)


def test_the_body_never_carries_a_birth_date_a_transcript_or_a_condition(
    session: BookingSession,
) -> None:
    """Spec §3.9: the email travels over ordinary mail, so what it may say is less than we know."""
    session.set_details(
        first_name="Anna",
        last_name="Weber",
        date_of_birth="1971-04-12",
        phone="0176 4289 9911",
        service_key="medizinische_fusspflege",
    )
    conversation = uuid.uuid4()

    body = body_for(
        session.summary(now=datetime(2026, 9, 7, 10, 30)),
        language="de",
        caller_number="+4917612345678",
        conversation_id=conversation,
    )

    assert "1971-04-12" not in body
    assert "call-abc123" in body
    assert conversation.hex in body
    assert "Audio wird nicht gespeichert" in body


def test_a_callback_appears_with_its_number_and_urgency(session: BookingSession) -> None:
    session.set_details(phone="0176 4289 9911")
    session.create_callback_request(reason="möchte einen Menschen sprechen", urgency="urgent_review")

    summary = session.summary()
    body = body_for(summary, language="de", caller_number=None, conversation_id=None)

    assert summary["outcome"] == OUTCOME_CALLBACK
    assert summary["staff_action_required"] is True
    assert "+4917642899911" in body
    assert "urgent_review" in body


def test_a_short_notice_cancellation_is_flagged_for_the_practice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Ausfallhonorar is decided case by case, so the practice has to be told which ones."""
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("call-soon")
    soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    session.calendar.create_appointment(
        Appointment(ref="soon", start=soon, raw={"resource": "MA1"}), patient_id=1
    )
    session._identity.update({"verified": True, "target_ref": "soon"})
    session.change_notices("cancel")
    session.confirm()

    assert session.cancel()["status"] == "ok"
    body = body_for(session.summary(), language="de", caller_number=None, conversation_id=None)

    assert "Kurzfristig:      ja" in body


def test_a_technical_error_outranks_everything_else_in_the_subject(
    session: BookingSession,
) -> None:
    """The one outcome that needs a human today (spec §6 rule 12)."""
    session.technical_error = "TimeoutError: calendar unreachable"

    summary = session.summary()

    assert summary["outcome"] == OUTCOME_ERROR
    assert summary["staff_action_required"] is True


@pytest.mark.anyio
async def test_an_unconfigured_mail_server_reports_it_rather_than_failing_silently(
    session: BookingSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A practice without an SMTP processor yet must still see what would have been sent."""
    monkeypatch.setattr("app.workloads.conversational.notifications.settings.smtp_host", None)

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery == {
        "sent": False,
        "reason": "smtp_not_configured",
        "recipients": list(load_practice().recipients),
    }


@pytest.mark.anyio
async def test_the_summary_goes_to_both_practice_mailboxes(
    session: BookingSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both, always (spec §3.9) — and from the knowledge base, not from an env var."""
    sent: list = []
    monkeypatch.setattr(
        "app.workloads.conversational.notifications.settings.smtp_host", "smtp.example.test"
    )
    monkeypatch.setattr(
        "app.workloads.conversational.notifications._send_smtp", lambda message: sent.append(message)
    )

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery["sent"] is True
    assert sent[0]["To"] == "annettedemko@gmail.com, muenchen@healthyfeet-podologie.de"


@pytest.mark.anyio
async def test_a_mail_failure_is_reported_and_never_raised(
    session: BookingSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """By the time this runs the caller has hung up; a dead mail server must not surface as one."""
    def explode(message):
        raise OSError("connection refused")

    monkeypatch.setattr(
        "app.workloads.conversational.notifications.settings.smtp_host", "smtp.example.test"
    )
    monkeypatch.setattr("app.workloads.conversational.notifications._send_smtp", explode)

    delivery = await send_call_summary(session.summary(), language="de")

    assert delivery["sent"] is False
    assert "connection refused" in delivery["reason"]


def test_a_booked_call_reports_the_appointment_it_created(session: BookingSession) -> None:
    from tests.test_voice_booking import _complete

    session.set_details(**_complete())
    session.find_patient()
    session.confirm()
    assert session.book()["status"] == "ok"

    summary = session.summary()
    body = body_for(summary, language="de", caller_number=None, conversation_id=None)

    assert summary["outcome"] == OUTCOME_BOOKED
    assert summary["appointment"]["start"] == session.draft["preferred_time"]
    assert summary["run_ids"]
    assert "Neuer Termin" in body


def test_a_verified_change_names_the_patient_it_was_made_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancellation the practice cannot attribute to anyone is a line they have to chase."""
    from tests.test_voice_booking import _booked_session, _verified

    caller = _verified(_booked_session(tmp_path, monkeypatch))
    caller.change_notices("cancel")
    caller.confirm()
    assert caller.cancel()["status"] == "ok"

    assert caller.summary()["patient_name"] == "Anna Weber"
    assert "Anna Weber" in subject_for(caller.summary())


def test_a_short_notice_change_is_marked_as_needing_staff_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The approved wording promises the practice will look at it case by case (spec §3.7)."""
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("call-soon")
    soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    session.calendar.create_appointment(
        Appointment(ref="soon", start=soon, raw={"resource": "MA1"}), patient_id=1
    )
    session._identity.update({"verified": True, "target_ref": "soon"})
    session.change_notices("cancel")
    session.confirm()
    assert session.cancel()["status"] == "ok"

    assert session.summary()["staff_action_required"] is True
