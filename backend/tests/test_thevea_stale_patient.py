"""A card created moments ago is not yet known to the session: log in again, write once more.

Live call of 27 Oct 2026: the booking created the caller's card (it is in thevea, active), then
thevea refused the appointment for it — `errorTypes: ['PATIENT']`, "Der hinterlegte Patient kann
nicht mehr gefunden werden", hint "Bitte lade die Seite neu". thevea's own answer says nothing was
created, so a fresh login and one more attempt cannot double-book; every other refusal still fails.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.base import Appointment
from app.providers.thevea import TheveaAbsence, TheveaConnector, TheveaError

_STALE = {
    "type": "ERROR",
    "createdTermineIds": [],
    "errorTypes": ["PATIENT"],
    "errors": [{
        "type": "PATIENT",
        "text": "Der hinterlegte Patient kann nicht mehr gefunden werden",
        "hinweis": "Bitte lade die Seite neu",
    }],
}
_OK = {"type": "SUCCESS", "createdTermineIds": [42]}


def _connector(answers: list[dict], calls: list[str], **opts) -> TheveaConnector:
    """thevea answering the appointment writes with `answers` in turn; every call is recorded."""

    def handler(request):
        body = json.loads(request.content)
        if body.get("operationName") == "addPatientenTermin":
            calls.append("write")
            result = answers.pop(0)
            return httpx.Response(200, json={"data": {"addPatientenTermin": {"validationResult": result}}})
        calls.append("login")
        return httpx.Response(
            200,
            json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}},
            headers={"set-cookie": "PHPSESSID=fresh; Path=/"},
        )

    return TheveaConnector(
        "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler), room_id=208416, **opts
    )


def _appt() -> Appointment:
    return Appointment(ref="call-1", start="2026-10-29 10:00:00+00", raw={"service_label": "Termin"})


def test_a_new_card_refused_as_not_found_is_written_after_a_fresh_login() -> None:
    calls: list[str] = []
    conn = _connector([_STALE, _OK], calls)

    conn.create_appointment(_appt(), patient_id=1001)

    assert calls == ["login", "write", "login", "write"]


def test_a_warm_session_is_dropped_and_the_new_one_cached() -> None:
    calls: list[str] = []
    cached: list[dict] = []
    conn = _connector(
        [_STALE, _OK], calls, session_cookies={"PHPSESSID": "old"}, on_login=cached.append
    )

    conn.create_appointment(_appt(), patient_id=1001)

    assert calls == ["write", "login", "write"]
    assert cached == [{"PHPSESSID": "fresh"}], "the fresh session replaces the stale one"


def test_it_tries_once_more_only() -> None:
    calls: list[str] = []
    conn = _connector([_STALE, _STALE], calls)

    with pytest.raises(TheveaError, match="Patient kann nicht mehr gefunden"):
        conn.create_appointment(_appt(), patient_id=1001)

    assert calls.count("write") == 2


def test_any_other_refusal_fails_at_once() -> None:
    calls: list[str] = []
    absent = {"type": "ERROR", "createdTermineIds": [], "errorTypes": ["ABWESENHEIT"]}
    conn = _connector([absent], calls)

    with pytest.raises(TheveaAbsence):
        conn.create_appointment(_appt(), patient_id=1)

    assert calls.count("write") == 1


def test_a_patient_error_that_created_something_is_not_written_again() -> None:
    """Only a refusal that created nothing is safe to repeat (append-only, ADR-0004 D7)."""
    calls: list[str] = []
    half = {**_STALE, "createdTermineIds": [7]}
    conn = _connector([half], calls)

    with pytest.raises(TheveaError):
        conn.create_appointment(_appt(), patient_id=1)

    assert calls.count("write") == 1
