"""The import must not book a visit the practice already has under an entry of its own.

Found live in November 2026: staff had typed a patient's surname one way at 10:00, Doctolib spelled
it another way at 10:00, and the import — which only looks for its own ref — created a second card
and a second appointment. It happened to several patients that month. The names below are made up.
"""

from __future__ import annotations

import json

import httpx
import pytest
from laufwise.state.base import StateUnavailable

from app.connectors.base import Appointment, Patient
from app.providers.appointment import DestinationMatchProvider
from app.providers.doctolib import DoctolibError
from app.providers.thevea import TheveaConnector, _likely_same_person
from app.sync.orchestrator import _review

AT = "2026-11-10T09:00:00.000Z"


@pytest.mark.parametrize(
    "source, card",
    [
        (("lena", "Wodaska"), ("Lena", "Wodachska")),
        (("Ada", "Kaya"), ("Ada Nur", "Kaya")),
        (("Otto", "Berger"), ("Otto", "Berger,")),
        (("Ines", "Lindqvist"), ("Ines", "Lindqvist")),
    ],
)
def test_the_same_person_written_differently_is_recognised(source, card):
    assert _likely_same_person(*source, *card)


@pytest.mark.parametrize(
    "source, card",
    [
        (("Anna", "Weber"), ("Anne", "Weber")),
        (("Maria", "Müller"), ("Mario", "Müller")),
        (("Paul", "Schneider"), ("Paul", "Bauer")),
        (("", "Schneider"), ("", "Schneider")),
    ],
)
def test_a_different_person_is_not(source, card):
    assert not _likely_same_person(*source, *card)


def _termin(start=AT, patient_id=7, status=None, termin_id=55):
    return {
        "__typename": "PatientenTermin", "id": termin_id, "from": start, "until": start,
        "bemerkung": "Pkb", "status": status, "mandantMitarbeiterId": 229566, "patientId": patient_id,
    }


def _connector(termine, cards):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        query = body.get("query") or ""
        if "Login" in query:
            response = httpx.Response(200, json={"data": {"login": {"erfolgreich": True}}})
            response.headers["set-cookie"] = "PHPSESSID=abc; Path=/"
            return response
        if "getTermine" in query:
            return httpx.Response(200, json={"data": {"termine": termine}})
        if "patientMitId" in query:
            return httpx.Response(200, json={"data": {"patientMitId": cards[body["variables"]["id"]]}})
        return httpx.Response(200, json={"data": {}})

    return TheveaConnector(
        "https://mein.thevea.de", "u", "p", search_room_ids=[208416, 229566],
        transport=httpx.MockTransport(handler),
    )


SOURCE_PATIENT = Patient(vorname="lena", nachname="Wodaska")
STAFF_CARD = {"vorname": "Lena", "nachname": "Wodachska"}


def test_the_practices_own_entry_at_the_same_minute_is_found():
    found = _connector([_termin()], {7: STAFF_CARD}).find_same_slot(SOURCE_PATIENT, "2026-11-10T10:00:00+01:00")

    assert found is not None and found.raw["id"] == 55
    assert found.raw["card_name"] == "Lena Wodachska"


@pytest.mark.parametrize(
    "termin",
    [
        _termin(start="2026-11-10T09:30:00.000Z"),  # another time
        _termin(status="ABGESAGT"),  # cancelled: the visit is not there any more
        {**_termin(), "patientId": None, "__typename": "SonstigerTermin"},  # not a patient
    ],
)
def test_nothing_else_counts_as_the_same_visit(termin):
    assert _connector([termin], {7: STAFF_CARD}).find_same_slot(SOURCE_PATIENT, AT) is None


class _Source:
    def __init__(self, appt=None, fails=False):
        self._appt, self._fails = appt, fails

    def get_appointment(self, ref):
        if self._fails:
            raise DoctolibError("doctolib down")
        return self._appt


class _Dest:
    def __init__(self, by_ref=None, by_slot=None):
        self._by_ref, self._by_slot = by_ref, by_slot
        self.slot_asked = []

    def find_appointment(self, ref):
        return self._by_ref

    def find_same_slot(self, patient, start):
        self.slot_asked.append((patient.vorname, patient.nachname, start))
        return self._by_slot


SOURCE_APPT = Appointment(
    ref="DL-1", start=AT, patient="lena Wodaska",
    raw={"first_name": "lena", "last_name": "Wodaska", "status": "confirmed"},
)
HAND_COPY = Appointment(ref="55", start=AT, raw={"id": 55, "card_name": "Lena Wodachska"})


def test_a_visit_already_booked_by_hand_counts_as_present():
    dest = _Dest(by_slot=HAND_COPY)
    view = DestinationMatchProvider(dest, "DL-1", _Source(SOURCE_APPT)).query("appointment_by_ref")

    assert view.value["matched_by"] == "slot" and view.value["id"] == 55
    assert dest.slot_asked == [("lena", "Wodaska", AT)]


def test_our_own_copy_is_found_by_ref_without_asking_for_the_slot():
    dest = _Dest(by_ref=Appointment(ref="DL-1", start=AT, raw={"id": 9}), by_slot=HAND_COPY)
    view = DestinationMatchProvider(dest, "DL-1", _Source(SOURCE_APPT)).query("appointment_by_ref")

    assert view.value["id"] == 9 and "matched_by" not in view.value and dest.slot_asked == []


def test_nothing_at_that_time_is_absent():
    view = DestinationMatchProvider(_Dest(), "DL-1", _Source(SOURCE_APPT)).query("appointment_by_ref")
    assert view.value is None


def test_an_unreadable_source_blocks_instead_of_reading_as_absent():
    with pytest.raises(StateUnavailable):
        DestinationMatchProvider(_Dest(), "DL-1", _Source(fails=True)).query("appointment_by_ref")


def test_a_skip_over_the_practices_own_entry_is_reported_with_both_names():
    review = _review(_Dest(by_slot=HAND_COPY), skipped=[SOURCE_APPT], gone=[])

    assert review == [
        {
            "kind": "already_booked",
            "ref": "DL-1",
            "patient": "lena Wodaska",
            "source_start": AT,
            "dest_start": AT,
            "dest_patient": "Lena Wodachska",
        }
    ]


def test_a_cancellation_whose_visit_staff_entered_by_hand_is_still_reported():
    gone = Appointment(ref="DL-1", start=AT, patient="lena Wodaska", raw={"status": "deleted"})
    (entry,) = _review(_Dest(by_slot=HAND_COPY), skipped=[], gone=[gone])

    assert entry["kind"] == "cancelled_in_source" and entry["dest_patient"] == "Lena Wodachska"
