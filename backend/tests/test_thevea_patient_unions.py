"""thevea answers a patient lookup and a new card with a union, not with the card itself.

Since 2026-11-05 `patientMitId` returns `PatientMitIdOutput` (the `Patient`, `PatientNichtGefunden`
or `DomainInvalid`) and `patientAnlegen` returns the `Patient` or a typed refusal. thevea rejects a
field selected directly on a union, so the same-minute check and every new patient card failed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.thevea import TheveaConnector, TheveaError
from app.connectors.base import Patient

AT = "2026-11-10T09:00:00.000Z"
_LOGIN = {"data": {"benutzerLogin": {"__typename": "EmailBenutzerZugang", "benutzerkennung": "u"}}}


def _connector(answers, seen):
    def handler(request):
        body = json.loads(request.content or b"{}")
        query = body.get("query") or ""
        for field, answer in answers.items():
            if field in query and "Login" not in query:
                seen.setdefault(field, []).append(query)
                return httpx.Response(200, json={"data": {field: answer}})
        return httpx.Response(200, json=_LOGIN)

    return TheveaConnector(
        "https://mein.thevea.de", "u", "p", search_room_ids=[229566],
        transport=httpx.MockTransport(handler),
    )


def _termine():
    return [{
        "__typename": "PatientenTermin", "id": 55, "from": AT, "until": AT, "bemerkung": "",
        "status": None, "mandantMitarbeiterId": 229566, "patientId": 7,
    }]


def test_the_patient_name_is_selected_through_a_fragment():
    seen: dict = {}
    conn = _connector({"termine": _termine(), "patientMitId": {
        "__typename": "Patient", "vorname": "Lena", "nachname": "Weber"}}, seen)

    found = conn.find_same_slot(Patient(vorname="Lena", nachname="Weber"), AT)

    assert found is not None and found.raw["card_name"] == "Lena Weber"
    assert "... on Patient { vorname nachname }" in seen["patientMitId"][0]


def test_a_missing_card_is_no_match():
    conn = _connector({"termine": _termine(), "patientMitId": {"__typename": "PatientNichtGefunden"}}, {})

    assert conn.find_same_slot(Patient(vorname="Lena", nachname="Weber"), AT) is None


def test_a_refused_lookup_raises_instead_of_reading_as_a_free_slot():
    conn = _connector({"termine": _termine(), "patientMitId": {"__typename": "DomainInvalid"}}, {})

    with pytest.raises(TheveaError, match="DomainInvalid"):
        conn.find_same_slot(Patient(vorname="Lena", nachname="Weber"), AT)


def _new_patient():
    return Patient(vorname="Lena", nachname="Weber", geburtsdatum="1988-01-26", source="doctolib",
                   source_ref="DL-abc")


def test_a_new_card_is_read_through_a_fragment():
    seen: dict = {}
    conn = _connector({"patientAnlegen": {
        "__typename": "Patient", "id": 9, "vorname": "Lena", "nachname": "Weber",
        "geburtsdatum": "1988-01-26"}}, seen)

    ref = conn.create_patient(_new_patient())

    assert ref.id == 9
    assert "... on Patient { id vorname nachname geburtsdatum }" in seen["patientAnlegen"][0]


@pytest.mark.parametrize("refusal", ["EingabeFehlerhaft", "PatientAnlegenFehlerListe"])
def test_a_refused_card_raises_with_its_type(refusal):
    conn = _connector({"patientAnlegen": {"__typename": refusal}}, {})

    with pytest.raises(TheveaError, match=refusal):
        conn.create_patient(_new_patient())
