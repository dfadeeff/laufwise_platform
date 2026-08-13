"""Governed patient cards (ADR-0005) — unit level, against httpx mock transports.

Covers the four things the ADR says must hold and that a mistake would break silently:
  1. a PATIENT-BOUND appointment is findable by its ref (the `Termin`-interface fix — miss it and
     idempotency inverts: duplicate on every re-run AND a postcondition that always fails);
  2. the card carries exactly the agreed fields, with reminders OFF and the source ref recorded;
  3. matching binds only on name + date of birth, and the sentinel date never counts as a match;
  4. `ABWESENHEIT` is a distinct outcome, and the placement ladder ends in a FORCED write.

No network and no database — these must run everywhere, unlike the DB-backed e2e suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.connectors.base import Appointment, Patient, patient_from_appointment
from app.providers.thevea import (
    SENTINEL_BIRTHDATE,
    _birthdate,
    TheveaAbsence,
    TheveaConnector,
)
from app.sync.orchestrator import _placement_plan, _should_try_another_room

_ROOMS = [208413, 208416, 229566]  # MA 1, MA 2, MA 3 — the only Munich rooms (ADR-0005 context)
_LOGIN = {"data": {"benutzerLogin": {"benutzerkennung": "u"}}}


def _thevea(handler, **opts):
    return TheveaConnector(
        "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler), **opts
    )


def _patient(**over) -> Patient:
    base = dict(
        vorname="Valentina",
        nachname="Zeller-Klaus",
        geburtsdatum="1988-01-26",
        strasse="Teststraße 1",
        plz="80331",
        ort="München",
        email="v@example.com",
        source_ref="DL-abc123",
        source="doctolib",
    )
    base.update(over)
    return Patient(**base)


def _router(handler_map):
    """Dispatch by operationName / query substring; everything else is the login response."""

    def handler(request):
        body = json.loads(request.content)
        key = body.get("operationName") or ""
        for name, fn in handler_map.items():
            if key == name or name in body.get("query", ""):
                return fn(body)
        return httpx.Response(200, json=_LOGIN)

    return handler


# --- 0. what each source hands over -------------------------------------------------------

def _hf(**over) -> Appointment:
    """A healthyfeet booking: ONE display name, a German date of birth, and the address composed
    into a single line — doctolib sends all three apart, so this is the shape that needs work."""
    raw = {"ref": "HF-a1", "preferred_date": "2026-08-10 09:00:00+00", "name": "Valentina Zeller-Klaus"}
    raw.update(over)
    return Appointment(ref=raw["ref"], start=raw["preferred_date"], patient=raw.get("name"), raw=raw)


@pytest.mark.parametrize(
    "whole,expected",
    [("Valentina Zeller-Klaus", ("Valentina", "Zeller-Klaus")),
     ("Anna Maria Müller", ("Anna Maria", "Müller")),
     ("Hans von Neumann", ("Hans", "von Neumann")),
     ("Ludwig van der Berg", ("Ludwig", "van der Berg")),
     ("Müller", ("", "Müller"))],
    ids=["two-parts", "two-first-names", "particle", "two-particles", "single-word"],
)
def test_a_composed_name_splits_on_the_surname(whole, expected):
    """healthyfeet's admin payload carries ONE `name`, composed from the form's two required
    fields, so the surname has to be recovered. It is what the destination's patient search is
    keyed on: get it wrong and we miss the card the practice typed by hand and open a duplicate.
    The last word is the surname — a multi-word FIRST name is far commoner than a multi-word
    surname — except after a nobiliary particle, which belongs to the surname."""
    got = patient_from_appointment(_hf(name=whole))
    assert (got.vorname, got.nachname) == expected


def test_the_sources_own_first_and_last_name_win_over_the_composed_one():
    """healthyfeet now carries `first_name`/`last_name` next to the composed `name` (its
    db/0006_name_split.sql), as doctolib always has. Those are the two fields the patient
    actually typed, so they must beat the split — which cannot tell this surname from a second
    first name, and would bind the appointment to the wrong card or open a duplicate."""
    got = patient_from_appointment(
        _hf(name="Maria Anna Sophie", first_name="Maria", last_name="Anna Sophie")
    )
    assert (got.vorname, got.nachname) == ("Maria", "Anna Sophie")


@pytest.mark.parametrize(
    "given,expected",
    [("26.01.1988", "1988-01-26"), ("26-01-1988", "1988-01-26"), ("1.2.1988", "1988-02-01"),
     ("1988-01-26", "1988-01-26")],
    ids=["dotted", "dashed", "unpadded", "already-iso"],
)
def test_german_dates_of_birth_reach_the_card_as_iso(given, expected):
    """healthyfeet carries the German day-first order. Left unparsed it becomes the destination's
    "unknown" sentinel, which never matches — so every visit of the same person would open a NEW
    card, the exact duplicate this feature exists to prevent (ADR-0005 D3/D4)."""
    assert patient_from_appointment(_hf(birth_date=given)).geburtsdatum == expected


def test_an_unreadable_date_of_birth_is_passed_on_untouched():
    """No guessing here: the destination owns "usable or not" and substitutes its sentinel."""
    assert patient_from_appointment(_hf(birth_date="k. A.")).geburtsdatum == "k. A."


@pytest.mark.parametrize(
    "line,expected",
    [("Teststr. 1, 80331 München", ("Teststr. 1", "80331", "München")),
     ("Teststr. 1 80331 München", ("Teststr. 1", "80331", "München")),
     ("Am Hang 12a, 82031 Grünwald", ("Am Hang 12a", "82031", "Grünwald"))],
    ids=["comma", "no-comma", "house-number-with-letter"],
)
def test_a_one_line_address_is_split_into_the_cards_three_fields(line, expected):
    """thevea's card has three separate fields; healthyfeet has one line. Without the split the
    whole line lands in the street field and postcode + city stay empty."""
    got = patient_from_appointment(_hf(address=line))
    assert (got.strasse, got.plz, got.ort) == expected


def test_an_address_without_a_postcode_stays_whole():
    """Anchored on the postcode, so an unrecognised line is kept verbatim rather than guessed
    apart into a wrong city."""
    got = patient_from_appointment(_hf(address="Teststr. 1"))
    assert (got.strasse, got.plz, got.ort) == ("Teststr. 1", None, None)


def test_separate_address_fields_are_never_re_split():
    """doctolib already sends street/zip/city apart — the fallback must not touch them."""
    appt = Appointment(ref="DL-x", start="", raw={"street": "Teststr. 1", "zip": "80331", "city": "München"})
    got = patient_from_appointment(appt)
    assert (got.strasse, got.plz, got.ort) == ("Teststr. 1", "80331", "München")


# --- 1. the Termin-interface fix ----------------------------------------------------------

def test_find_appointment_sees_patient_bound_termin():
    """`bemerkung` lives on the `Termin` INTERFACE. Selecting it only inside
    `... on SonstigerTermin` makes a PatientenTermin invisible — which would make the idempotency
    precondition always pass and the verification postcondition always fail."""
    def termine(_body):
        return httpx.Response(200, json={"data": {"termine": [
            {"__typename": "PatientenTermin", "id": 42, "from": "2026-08-03T13:00:00Z",
             "bemerkung": "Nagelpflege · DL-abc123", "mandantMitarbeiterId": 208413,
             "patientId": 17017443},
        ]}})

    found = _thevea(_router({"getTermine": termine})).find_appointment("DL-abc123")
    assert found is not None, "a patient-bound appointment must be findable by its ref"
    assert found.ref == "DL-abc123"


def test_find_appointment_still_sees_legacy_room_termin():
    """v2-imported appointments stay `SonstigerTermin` and must remain findable (ADR-0005: they
    are deliberately not migrated), so the interface-level selection must cover both types."""
    def termine(_body):
        return httpx.Response(200, json={"data": {"termine": [
            {"__typename": "SonstigerTermin", "id": 7, "from": "2026-07-14T09:00:00Z",
             "bemerkung": "+49 · HF-a1", "mandantMitarbeiterId": 208413},
        ]}})

    assert _thevea(_router({"getTermine": termine})).find_appointment("HF-a1") is not None


# --- 2. the card payload ------------------------------------------------------------------

def test_create_patient_payload_is_exactly_the_agreed_fields():
    """Owner's complete list: names, DOB, street/postcode/city, email — nothing more. Reminders
    MUST be off (the agent may never make thevea mail or text a patient), the insurance wrapper is
    sent empty, and the card records where it came from."""
    captured: dict = {}

    def anlegen(body):
        captured.update(body["variables"]["input"])
        return httpx.Response(200, json={"data": {"patientAnlegen": {
            "id": 17017443, "vorname": "Valentina", "nachname": "Zeller-Klaus",
            "geburtsdatum": "1988-01-26T00:00:00Z"}}})

    ref = _thevea(_router({"patientAnlegen": anlegen})).create_patient(_patient())

    assert ref.id == 17017443
    assert captured["vorname"] == "Valentina" and captured["nachname"] == "Zeller-Klaus"
    assert captured["anschrift"] == {
        "strasseUndHausnummer": "Teststraße 1", "postleitzahl": "80331", "ort": "München",
    }
    assert captured["kontakt"] == {"email": "v@example.com"}
    assert captured["krankenversicherung"] == {}  # required wrapper, no content needed
    assert captured["terminErinnerungPerEmail"] is False
    assert captured["terminErinnerungPerSMS"] is False
    assert "DL-abc123" in captured["bemerkung"]  # our own creations stay findable by exact query
    assert captured["geburtsdatum"].startswith("1988-01-26")


@pytest.mark.parametrize(
    "given",
    [None, "", "2099-01-01", "1830-05-05"],
    ids=["missing", "blank", "in-the-future", "older-than-120y"],
)
def test_create_patient_substitutes_sentinel_for_unusable_dob(given):
    """thevea requires a date and rejects one in the future or >120y old, but the practice often
    omits it or types junk. One FIXED sentinel (not 'any implausible date') so such cards are
    findable with a single query."""
    captured: dict = {}

    def anlegen(body):
        captured.update(body["variables"]["input"])
        return httpx.Response(200, json={"data": {"patientAnlegen": {"id": 1}}})

    _thevea(_router({"patientAnlegen": anlegen})).create_patient(_patient(geburtsdatum=given))

    assert captured["geburtsdatum"].startswith(SENTINEL_BIRTHDATE)
    assert "unbekannt" in captured["bemerkung"].lower(), "a substituted DOB must be marked on the card"


def test_age_limit_is_compared_by_date_not_by_year():
    """thevea's limit is "not more than 120 years ago" to the DAY. A year-only comparison accepts
    dates thevea then rejects, and the card creation fails — so the boundary is tested explicitly."""
    today = datetime.now(timezone.utc).date()
    just_inside = today.replace(year=today.year - 120) + timedelta(days=1)
    just_outside = today.replace(year=today.year - 120) - timedelta(days=1)

    kept, substituted = _birthdate(just_inside.isoformat())
    assert not substituted and kept.startswith(just_inside.isoformat())

    _sub, substituted = _birthdate(just_outside.isoformat())
    assert substituted, "a date beyond thevea's 120-year limit must fall back to the sentinel"


# --- 3. matching --------------------------------------------------------------------------

def _uebersicht(nodes):
    return lambda _b: httpx.Response(
        200, json={"data": {"patientUebersicht": {"nodes": nodes, "pageInfo": {"nodesCount": len(nodes)}}}}
    )


def test_find_patient_binds_on_name_and_dob():
    nodes = [{"id": 501, "vorname": "Valentina", "nachname": "Zeller-Klaus",
              "geburtsdatum": "1988-01-26T00:00:00Z"}]
    found = _thevea(_router({"patientenUebersicht": _uebersicht(nodes)})).find_patient(_patient())
    assert found is not None and found.id == 501


def test_find_patient_refuses_name_only_match():
    """Same surname, different date of birth -> a DIFFERENT human. Owner's rule: prefer creating a
    duplicate over attaching one person's appointment to another's card."""
    nodes = [{"id": 502, "vorname": "Valentina", "nachname": "Zeller-Klaus",
              "geburtsdatum": "1975-09-02T00:00:00Z"}]
    assert _thevea(_router({"patientenUebersicht": _uebersicht(nodes)})).find_patient(_patient()) is None


def test_find_patient_never_matches_on_the_sentinel_date():
    """Both sides carry the placeholder DOB. Treating that as agreement would collapse every
    unknown-DOB patient sharing a surname into one card — the exact mis-attachment the rule exists
    to prevent (ADR-0005 D4)."""
    nodes = [{"id": 503, "vorname": "Valentina", "nachname": "Zeller-Klaus",
              "geburtsdatum": f"{SENTINEL_BIRTHDATE}T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht(nodes)}))
    assert conn.find_patient(_patient(geburtsdatum=None)) is None


def test_find_patient_ignores_email_only_agreement():
    """Families and relatives share one address, so email may corroborate a name+DOB match but
    must never establish one alone."""
    nodes = [{"id": 504, "vorname": "Bernd", "nachname": "Anders",
              "geburtsdatum": "1970-03-03T00:00:00Z", "email": "v@example.com"}]
    assert _thevea(_router({"patientenUebersicht": _uebersicht(nodes)})).find_patient(_patient()) is None


def test_find_patient_returns_none_when_nothing_matches():
    assert _thevea(_router({"patientenUebersicht": _uebersicht([])})).find_patient(_patient()) is None


# --- 4. the patient-bound write, absence, and forcing -------------------------------------

def _appt() -> Appointment:
    return Appointment(
        ref="DL-abc123", start="2026-08-03 13:00:00+00", patient="Valentina Zeller-Klaus",
        raw={"service_label": "Nagelpflege", "status": "confirmed", "phone": "+4915100000000"},
    )


def _add_termin(captured, *, result):
    def fn(body):
        captured.update(body["variables"]["input"]["terminInput"])
        return httpx.Response(200, json={"data": {"addPatientenTermin": {"validationResult": result}}})

    return fn


def test_create_appointment_writes_a_patient_bound_termin():
    """`PatientenTermin` has no `title`, so the procedure name goes to `bemerkung` with the ref
    LAST (find_appointment matches `ref in bemerkung`)."""
    captured: dict = {}
    conn = _thevea(
        _router({"addPatientenTermin": _add_termin(captured, result={"type": "SUCCESS", "createdTermineIds": [9]})}),
        room_id=208416,
    )
    conn.create_appointment(_appt(), patient_id=17017443)

    assert captured["patientenId"] == 17017443
    assert captured["mandantMitarbeiterId"] == 208416
    assert captured["patientenTerminArt"] == "PRAXIS"
    assert "title" not in captured, "PatientenTermin has no title field"
    assert captured["bemerkung"].endswith("DL-abc123")
    assert "Nagelpflege" in captured["bemerkung"]
    # The phone belongs to the appointment, not the card: the reason to reach for it is a patient
    # who has to be called back about THIS visit, and the card carries the agreed fields only.
    assert "+4915100000000" in captured["bemerkung"]
    assert captured["ignoreValidation"] is False


def test_absence_is_a_distinct_error_not_a_generic_failure():
    """The orchestrator has to tell 'this room is on holiday' apart from 'the write failed', so
    ABWESENHEIT must surface as its own type."""
    conn = _thevea(_router({"addPatientenTermin": _add_termin({}, result={
        "type": "ERROR", "createdTermineIds": [], "errorTypes": ["ABWESENHEIT"]})}))
    with pytest.raises(TheveaAbsence):
        conn.create_appointment(_appt(), patient_id=1)


def test_forced_write_sets_ignore_validation_and_is_marked():
    """Last rung of the ladder: thevea's own UI allows this behind an 'are you sure?' — but a
    forced write must be visible in the calendar, not silently identical to a normal one."""
    captured: dict = {}
    conn = _thevea(_router({"addPatientenTermin": _add_termin(captured, result={
        "type": "SUCCESS", "createdTermineIds": [11]})}))
    conn.create_appointment(_appt(), patient_id=1, force=True)

    assert captured["ignoreValidation"] is True
    assert captured["bemerkung"].endswith("DL-abc123")  # ref stays last — idempotency unchanged
    assert "ARBEITSZEIT" in captured["bemerkung"].upper(), "a forced write must be marked"


# --- the placement ladder (pure, no I/O) --------------------------------------------------

def test_placement_plan_tries_assigned_room_then_the_others():
    plan = _placement_plan(_ROOMS, assigned=208416)
    assert [room for room, _forced in plan][:3] == [208416, 208413, 229566]
    assert [forced for _room, forced in plan][:3] == [False, False, False]


def test_placement_plan_ends_with_a_single_forced_attempt():
    """If every room refuses, the appointment is written anyway — once, into the room it was
    assigned to — so the owner sees it in the calendar and decides."""
    plan = _placement_plan(_ROOMS, assigned=229566)
    assert plan[-1] == (229566, True)
    assert sum(1 for _room, forced in plan if forced) == 1
    assert len(plan) == len(_ROOMS) + 1


def test_placement_plan_handles_a_single_room():
    assert _placement_plan([208413], assigned=208413) == [(208413, False), (208413, True)]


def test_only_a_rejected_placement_moves_to_another_room():
    """The orchestrator cannot see WHY the write failed — a tool's note never reaches it; the
    engine reports the postcondition's reason (laufwise `engine/local.py:241`). So the retry keys
    off the only signal available, and must not retry the three outcomes another room can't fix."""
    assert _should_try_another_room("rejected")          # write did not land — another room may take it
    assert not _should_try_another_room("ok")            # placed
    assert not _should_try_another_room("blocked")       # already imported — a skip, not a placement problem
    assert not _should_try_another_room("state_unavailable")  # system is down; don't hammer it


# --- the "already past" cutoff --------------------------------------------------------------

def test_an_earlier_hour_of_today_is_still_importable():
    """Asking for today means the whole of today. An appointment at 09:00 is precisely the one
    being invoiced at 11:00 — refusing it because the hour has passed makes the import useless for
    the appointments the practice actually bills."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from app.sync.orchestrator import _exclude_reason

    berlin = ZoneInfo("Europe/Berlin")
    now = datetime.now(timezone.utc).astimezone(berlin).replace(hour=11, minute=0, second=0, microsecond=0)
    at_nine = now.replace(hour=9)
    appt = Appointment(ref="HF-x", start=at_nine.isoformat(), raw={"status": "confirmed"})

    assert _exclude_reason(appt, now.astimezone(timezone.utc)) is None

    # Yesterday stays excluded — the cutoff is one day wide, not "any past".
    yesterday = Appointment(
        ref="HF-y", start=(at_nine - timedelta(days=1)).isoformat(), raw={"status": "confirmed"}
    )
    assert _exclude_reason(yesterday, now.astimezone(timezone.utc)) == "in the past"


# --- matching: spelling differences vs different people ------------------------------------

def _uebersicht_letter(nodes):
    """thevea's patient list, now fetched by the surname's first letter."""
    return lambda _b: httpx.Response(
        200, json={"data": {"patientUebersicht": {"nodes": nodes, "pageInfo": {"nodesCount": len(nodes)}}}}
    )


@pytest.mark.parametrize(
    "stored,given",
    [(("Valentina", "Müller"), ("Valentina", "Mueller")),
     (("Valentina", "Mueller"), ("Valentina", "Müller")),
     (("Jörg", "Weiß"), ("Joerg", "Weiss")),
     (("Valentina", "Zeller-Klaus"), ("Valentina", "Zeller Klaus"))],
    ids=["ue-to-umlaut", "umlaut-to-ue", "two-umlauts", "hyphen-vs-space"],
)
def test_transliterated_umlauts_are_the_same_person(stored, given):
    """`Müller` and `Mueller` are one name written two ways — the transliteration German uses when
    a form cannot carry the umlaut. Treating them as two people opens a second card for a patient
    who already has one."""
    nodes = [{"id": 601, "vorname": stored[0], "nachname": stored[1],
              "geburtsdatum": "1988-01-26T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht_letter(nodes)}))
    found = conn.find_patient(_patient(vorname=given[0], nachname=given[1]))
    assert found is not None and found.id == 601


def test_one_typo_in_a_long_name_still_matches():
    """A single slipped letter with an agreeing date of birth is a typo, not another human."""
    nodes = [{"id": 602, "vorname": "Valentina", "nachname": "Zeller",
              "geburtsdatum": "1988-01-26T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht_letter(nodes)}))
    assert conn.find_patient(_patient(nachname="Zeler")) is not None


def test_two_differences_at_once_are_a_different_person():
    """One near-miss is a slip; two is someone else. Prefer a duplicate card over a wrong bind."""
    nodes = [{"id": 603, "vorname": "Valentino", "nachname": "Zeler",
              "geburtsdatum": "1988-01-26T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht_letter(nodes)}))
    assert conn.find_patient(_patient(nachname="Zeller")) is None


def test_a_typo_in_a_short_name_matches_too():
    """No length floor: one considered as a twin guard was rejected by the owner (twins are not
    named that alike), and it was costing real matches on short names. What still protects against
    a wrong bind is that only ONE of the two names may differ."""
    nodes = [{"id": 604, "vorname": "Anna", "nachname": "Zeller-Klaus",
              "geburtsdatum": "1988-01-26T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht_letter(nodes)}))
    assert conn.find_patient(_patient(vorname="Anne")) is not None


def test_a_typo_never_overrides_a_different_date_of_birth():
    nodes = [{"id": 605, "vorname": "Valentina", "nachname": "Zeler",
              "geburtsdatum": "1975-09-02T00:00:00Z"}]
    conn = _thevea(_router({"patientenUebersicht": _uebersicht_letter(nodes)}))
    assert conn.find_patient(_patient()) is None


def test_the_patient_list_is_read_once_per_term():
    """An import runs one contract per appointment; re-reading the same searches for each would be
    the same queries dozens of times over. Two terms per surname (the name and its initial), then
    nothing more however many times the same surname comes up."""
    calls: list[int] = []

    def uebersicht(_b):
        calls.append(1)
        return httpx.Response(200, json={"data": {"patientUebersicht": {
            "nodes": [{"id": 606, "vorname": "Valentina", "nachname": "Zeller-Klaus",
                       "geburtsdatum": "1988-01-26T00:00:00Z"}], "pageInfo": {"nodesCount": 1}}}})

    conn = _thevea(_router({"patientenUebersicht": uebersicht}))
    for _ in range(3):
        assert conn.find_patient(_patient()) is not None
    assert len(calls) == 2, "the surname and its initial — never once per appointment"


def test_a_server_that_declines_short_searches_still_finds_the_card():
    """Regression: looking up ONLY by the surname's initial came back empty from the live account,
    so a card that had just been written could not be verified and every import failed. The
    surname as written is the search thevea is built for — it must stay in the mix."""
    def uebersicht(body):
        term = body["variables"]["tabellenInput"]["search"]
        if len(term) < 2:  # the live server's behaviour: nothing for a one-character search
            return httpx.Response(200, json={"data": {"patientUebersicht": {
                "nodes": [], "pageInfo": {"nodesCount": 0}}}})
        return httpx.Response(200, json={"data": {"patientUebersicht": {
            "nodes": [{"id": 701, "vorname": "Valentina", "nachname": "Zeller-Klaus",
                       "geburtsdatum": "1988-01-26T00:00:00Z"}], "pageInfo": {"nodesCount": 1}}}})

    conn = _thevea(_router({"patientenUebersicht": uebersicht}))
    found = conn.find_patient(_patient(), strict=False)
    assert found is not None and found.id == 701
