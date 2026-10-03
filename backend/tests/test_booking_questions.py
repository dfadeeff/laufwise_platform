"""A practice's own booking questions are asked, required ones are enforced, and the answers land
in the appointment's note (ADR-0020).

Generic on purpose: "Welche Behandlung?" and "Haben Sie ein Rezept?" are a podiatrist's questions,
not the platform's. The practice writes them; the platform asks, checks and records.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession

_IDENTITY = dict(
    practice_name="Praxis Podo", street="Baumkirchner Straße 19", postcode="81673", city="München",
    phone="+498941115335", recipients=["team@example.org"], consent_policy_id="policy-v1",
)
_QUESTIONS = [
    {"label": "Behandlung", "ask": "Welche Behandlung wünschen Sie?"},
    {"label": "Rezept", "ask": "Haben Sie ein Rezept?"},
    {"label": "Hinweis", "ask": "Gibt es etwas, das wir wissen sollten?", "required": False},
]


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _slot() -> str:
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return f"{day.isoformat()}T09:00"


def _session(questions=_QUESTIONS) -> BookingSession:
    config = AgentConfig(**_IDENTITY, booking_questions=questions)
    return BookingSession("questions", practice=config.to_practice())


def _details(session: BookingSession, **extra) -> dict:
    return session.set_details(
        first_name="Max", last_name="Bookman", date_of_birth="1992-07-13",
        phone="0151 5983 2613", preferred_time=_slot(), **extra,
    )


def _confirm_and_book(session: BookingSession) -> dict:
    session.find_patient()
    session.confirm("Max Bookman, neun Uhr.")
    return session.book()


def test_the_agent_is_told_to_ask_the_practice_s_next_question() -> None:
    session = _session()

    notes = " ".join(_details(session)["agent_notes"])

    assert "Welche Behandlung wünschen Sie?" in notes and '"Behandlung"' in notes


def test_a_booking_without_a_required_answer_is_refused_and_nothing_is_written() -> None:
    session = _session()
    _details(session, answers={"Behandlung": "Hornhautentfernung"})

    result = _confirm_and_book(session)

    assert result["status"] == "blocked" and result["unanswered"] == ["Rezept"]
    assert "Haben Sie ein Rezept?" in " ".join(result["agent_notes"])
    assert session.calendar.appointments == []


def test_the_answers_are_written_into_the_appointment_s_note_in_the_practice_s_order() -> None:
    session = _session()
    _details(session, answers={"rezept": "ja, Muster 13", "Behandlung": "Hornhautentfernung"})

    assert _confirm_and_book(session)["status"] == "ok"
    (appointment,) = session.calendar.appointments
    assert appointment.raw["details"] == "Behandlung: Hornhautentfernung · Rezept: ja, Muster 13"


def test_an_optional_question_does_not_block_the_booking() -> None:
    session = _session()
    _details(session, answers={"Behandlung": "Nagelpilz", "Rezept": "nein"})

    assert _confirm_and_book(session)["status"] == "ok"


def test_an_answer_to_a_question_the_practice_never_set_is_refused() -> None:
    session = _session()

    result = _details(session, answers={"Versicherung": "AOK"})

    assert "Versicherung" in result["rejected"] and "Versicherung" not in session.answers


def test_changing_an_answer_after_the_read_back_needs_a_new_confirmation() -> None:
    session = _session()
    _details(session, answers={"Behandlung": "Hornhaut", "Rezept": "nein"})
    session.find_patient()
    session.confirm("Max Bookman, neun Uhr.")

    session.set_details(answers={"Behandlung": "Nagelpilz"})

    assert not session.confirmed


def test_an_agent_without_questions_books_as_before() -> None:
    session = _session(questions=[])
    result = _details(session)

    assert "unanswered" not in result
    assert _confirm_and_book(session)["status"] == "ok"
    assert session.calendar.appointments[0].raw["details"] == ""


def test_question_labels_must_be_distinct() -> None:
    with pytest.raises(ValidationError, match="own label"):
        AgentConfig(booking_questions=[{"label": "Rezept", "ask": "a"}, {"label": "rezept", "ask": "b"}])


def test_the_prompt_lists_the_practice_s_questions() -> None:
    from app.workloads.conversational.surface import _instructions

    prompt = _instructions("de", AgentConfig(**_IDENTITY, booking_questions=_QUESTIONS))

    assert '- Behandlung: "Welche Behandlung wünschen Sie?" (required)' in prompt
    assert '- Hinweis: "Gibt es etwas, das wir wissen sollten?" (optional' in prompt


def test_thevea_writes_the_answers_into_bemerkung_with_the_ref_last() -> None:
    from app.connectors.base import Appointment
    from app.providers.thevea import TheveaConnector

    sent: dict = {}

    def handler(request):
        body = json.loads(request.content)
        if body.get("operationName") == "addPatientenTermin":
            sent.update(body["variables"]["input"]["terminInput"])
            return httpx.Response(
                200,
                json={"data": {"addPatientenTermin": {"validationResult": {"type": "SUCCESS", "createdTermineIds": [1]}}}},
            )
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    conn = TheveaConnector("https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler))
    conn.create_appointment(
        Appointment(
            ref="HF-a1", start="2026-07-14 09:00:00+00", type="Termin", patient="Bookman",
            raw={"details": "Behandlung: Hornhautentfernung · Rezept: ja"},
        ),
        patient_id=500,
    )

    assert sent["bemerkung"] == "Termin · Behandlung: Hornhautentfernung · Rezept: ja · HF-a1"


def test_a_blank_question_is_refused_with_a_sentence_a_practice_understands() -> None:
    with pytest.raises(ValidationError, match="label and what the agent asks"):
        AgentConfig(booking_questions=[{"label": "Rezept", "ask": " "}])
