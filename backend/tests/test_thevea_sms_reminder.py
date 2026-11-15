"""A booking Mia makes in thevea turns on the patient's "Terminerinnerung per SMS" (#215).

In thevea the reminder is not a field of the appointment: it is a flag on the patient card, set
with `patientTerminErinnerungSetzen`, and thevea refuses it without a mobile number
(`PatientTerminErinnerungHandynummerFehlt`). So after the appointment is written, the card is read
and the flag is set with the mobile number the card already holds. Everything else on the card —
the email reminder, the email address — is sent back as it was read.

The appointment is the booking. A reminder that cannot be set never undoes it or fails the call.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx

from app.connectors.base import Appointment
from app.providers.thevea import TheveaConnector
from app.providers.thevea_calendar import TheveaPracticeCalendar
from app.workloads.conversational.practice import load_practice

ROOMS = {"MA1": 101, "MA2": 102, "MA3": 103, "MA4": 104}
_LOGIN = {"data": {"benutzerLogin": {"__typename": "EmailBenutzerZugang", "benutzerkennung": "u"}}}


def _card(*, telefon=None, handy=None, sms=False, email_flag=False, email=None):
    return {
        "__typename": "Patient",
        "terminErinnerungPerEmail": email_flag,
        "terminErinnerungPerSMS": sms,
        "kontakt": {"email": email, "telefonnummer": telefon, "handynummer": handy},
    }


def _calendar(card, *, reminder_answer=None):
    sent: list[dict] = []
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        query = body.get("query") or ""
        if "Login" in query:
            response = httpx.Response(200, json=_LOGIN)
            response.headers["set-cookie"] = "PHPSESSID=abc; Path=/"
            return response
        if body.get("operationName") == "addPatientenTermin":
            created.append(body)
            return httpx.Response(200, json={"data": {"addPatientenTermin": {
                "validationResult": {"type": "SUCCESS", "createdTermineIds": [9001]}}}})
        if "patientTerminErinnerungSetzen" in query:
            sent.append(body["variables"]["input"])
            answer = reminder_answer or {"__typename": "Patient", "terminErinnerungPerSMS": True}
            return httpx.Response(200, json={"data": {"patientTerminErinnerungSetzen": answer}})
        if "patientMitId" in query:
            return httpx.Response(200, json={"data": {"patientMitId": card}})
        return httpx.Response(200, json={"data": {}})

    connector = TheveaConnector(
        "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler)
    )
    return TheveaPracticeCalendar(connector, ROOMS), sent, created


def _book(calendar) -> None:
    day = date.today() + timedelta(days=7)
    while not load_practice().schedule.is_open(day):
        day += timedelta(days=1)
    calendar.create_appointment(
        Appointment(ref="voice-abc", start=f"{day}T10:00", raw={"resource": "MA2"}),
        patient_id=7,
    )


def test_a_booking_turns_on_the_sms_reminder_with_the_mobile_number_on_the_card() -> None:
    calendar, sent, created = _calendar(_card(telefon="+4915112345678", email="a@b.de"))

    _book(calendar)

    assert len(created) == 1
    assert sent == [{
        "patientId": 7,
        "terminErinnerungPerSMS": True,
        "handynummer": "+4915112345678",
        # Sent back exactly as read: turning on SMS must not switch off an email reminder.
        "terminErinnerungPerEmail": False,
        "email": "a@b.de",
    }]


def test_the_cards_own_mobile_number_wins_over_its_general_number() -> None:
    calendar, sent, _ = _calendar(
        _card(telefon="+49891234567", handy="+4917699887766", email_flag=True, email="a@b.de")
    )

    _book(calendar)

    (given,) = sent
    assert given["handynummer"] == "+4917699887766"
    assert given["terminErinnerungPerEmail"] is True


def test_a_reminder_already_on_is_left_alone() -> None:
    calendar, sent, created = _calendar(_card(handy="+4915112345678", sms=True))

    _book(calendar)

    assert created and sent == []


def test_a_landline_is_never_written_as_the_mobile_number() -> None:
    """An SMS cannot reach a landline, and writing one into `handynummer` is a wrong claim on the
    card. Without a mobile number the appointment stands and no reminder is set."""
    calendar, sent, created = _calendar(_card(telefon="+49891234567"))

    _book(calendar)

    assert created and sent == []


def test_a_refused_reminder_never_undoes_or_fails_the_booking() -> None:
    calendar, sent, created = _calendar(
        _card(telefon="+4915112345678"), reminder_answer={"__typename": "EingabeFehlerhaft"}
    )

    _book(calendar)  # does not raise

    assert created and len(sent) == 1


def test_an_unreadable_card_never_fails_the_booking() -> None:
    calendar, sent, created = _calendar({"__typename": "PatientNichtGefunden"})

    _book(calendar)

    assert created and sent == []


def test_the_connector_reports_whether_the_reminder_is_on() -> None:
    calendar, _, _ = _calendar(_card(telefon="+4915112345678"))
    assert calendar._connector.enable_sms_reminder(7) is True

    calendar, _, _ = _calendar(_card(telefon="+49891234567"))
    assert calendar._connector.enable_sms_reminder(7) is False
