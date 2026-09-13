"""The voice agent against the practice's REAL calendar, driven over a mock transport.

The point of these is the claim the whole seam rests on: the agent cannot tell which calendar it
is talking to. The same `BookingSession`, the same tools, the same governed contracts run against
thevea here as run against the in-memory sandbox everywhere else — only the transport differs
(ADR-0004). Where thevea genuinely cannot do something, that has to surface as a capability the
agent can see, not as a lie the caller hears.

No network and no real login: every GraphQL round trip is answered by `httpx.MockTransport`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import pytest

from app.connectors.base import (
    Appointment,
    AppointmentLifecycle,
    Patient,
    PracticeCalendar,
)
from app.providers.thevea import TheveaConnector, TheveaError
from app.providers.thevea_calendar import (
    TheveaCalendarUnconfigured,
    TheveaPracticeCalendar,
)
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.practice import load_practice

ROOMS = {"MA1": 101, "MA2": 102, "MA3": 103}


def _next_open(offset_days: int = 7) -> date:
    schedule = load_practice().schedule
    day = date.today() + timedelta(days=offset_days)
    while not schedule.is_open(day):
        day += timedelta(days=1)
    return day


def _utc_instant(local: str) -> str:
    """A local `YYYY-MM-DDTHH:MM` as the UTC Instant thevea would return."""
    from zoneinfo import ZoneInfo

    berlin = datetime.strptime(local, "%Y-%m-%dT%H:%M").replace(
        tzinfo=ZoneInfo(load_practice().schedule.timezone)
    )
    return berlin.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _calendar(termine=None, patients=None, on_create=None):
    """A thevea calendar whose server answers with the given appointments and patient cards."""
    recorded: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        query = body.get("query") or ""
        operation = body.get("operationName") or ""
        if "Login" in query:
            response = httpx.Response(200, json={"data": {"login": {"erfolgreich": True}}})
            response.headers["set-cookie"] = "PHPSESSID=abc; Path=/"
            return response
        if "getTermine" in query:
            return httpx.Response(200, json={"data": {"termine": list(termine or [])}})
        if "patientenUebersicht" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "patientUebersicht": {
                            "nodes": list(patients or []),
                            "pageInfo": {"nodesCount": len(patients or [])},
                        }
                    }
                },
            )
        if operation == "addPatientenTermin" or "addPatientenTermin" in query:
            recorded.append(body)
            if on_create:
                on_create(body)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "addPatientenTermin": {
                            "validationResult": {"type": "SUCCESS", "createdTermineIds": [9001]}
                        }
                    }
                },
            )
        return httpx.Response(200, json={"data": {}})

    connector = TheveaConnector(
        "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler)
    )
    calendar = TheveaPracticeCalendar(connector, ROOMS)
    return calendar, recorded


# --- the port, and the capability it deliberately lacks ---


def test_the_thevea_calendar_satisfies_the_port_the_agent_binds_to() -> None:
    calendar, _ = _calendar()

    assert isinstance(calendar, PracticeCalendar)


def test_the_thevea_calendar_cannot_change_an_appointment_and_says_so() -> None:
    """ADR-0008: the lifecycle is opt-in, and thevea has given us no mutation for it.

    The capability is ABSENT rather than stubbed, so the agent finds out by asking the seam —
    and a caller gets a callback instead of a cancellation that never happened.
    """
    calendar, _ = _calendar()

    assert not isinstance(calendar, AppointmentLifecycle)
    assert not hasattr(calendar, "cancel_appointment")
    assert not hasattr(calendar, "reschedule_appointment")


def test_a_session_on_thevea_refuses_to_cancel_rather_than_pretending(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    calendar, _ = _calendar()
    session = BookingSession("thevea-call", calendar=calendar)

    assert session.can_change_appointments is False
    result = session.cancel()

    assert result["status"] == "unavailable"
    assert "cannot be changed by phone" in result["reason"]
    assert any("call them back" in note for note in result["agent_notes"])


def test_unmapped_rooms_refuse_to_construct_rather_than_guessing_one() -> None:
    """Booking into a room id nobody gave us is booking into a stranger's calendar."""
    connector, _ = _calendar()

    with pytest.raises(TheveaCalendarUnconfigured) as raised:
        TheveaPracticeCalendar(connector._connector, {"MA1": 101})

    assert "MA2" in str(raised.value) and "MA3" in str(raised.value)


# --- availability is derived, and the practice's own rules apply to it ---


def test_availability_is_the_practice_grid_minus_what_thevea_holds() -> None:
    """thevea has no free-slot query, so the subtraction IS the availability."""
    day = _next_open()
    booked = [
        {
            "id": 1,
            "from": _utc_instant(f"{day}T09:00"),
            "until": _utc_instant(f"{day}T09:30"),
            "mandantMitarbeiterId": room,
            "status": "gebucht",
        }
        for room in ROOMS.values()
    ]
    calendar, _ = _calendar(termine=booked)

    slots = calendar.free_slots(
        date_from=day, date_to=day, limit=3, now=datetime.combine(day, datetime.min.time())
    )

    assert slots
    # 09:00 is taken on every calendar, so it is not offered on any of them.
    assert all(slot.start != f"{day}T09:00" for slot in slots)
    assert slots[0].start == f"{day}T09:30"


def test_the_break_is_never_offered_on_the_real_calendar_either() -> None:
    """The 12:00–13:00 break lives in the practice grid, so it is absent from thevea's slots for
    exactly the reason it is absent from the sandbox's: it is never generated."""
    day = _next_open()
    calendar, _ = _calendar()

    slots = calendar.free_slots(
        date_from=day, date_to=day, limit=99, now=datetime.combine(day, datetime.min.time())
    )

    assert slots
    assert all(not s.start.endswith(("T12:00", "T12:30")) for s in slots)
    assert {s.resource for s in slots} <= set(ROOMS)


def test_a_cancelled_appointment_frees_its_slot_again() -> None:
    day = _next_open()
    cancelled = [
        {
            "id": 1,
            "from": _utc_instant(f"{day}T09:00"),
            "until": _utc_instant(f"{day}T09:30"),
            "mandantMitarbeiterId": 101,
            "status": "abgesagt",
        }
    ]
    calendar, _ = _calendar(termine=cancelled)

    assert calendar.any_resource_free(f"{day}T09:00") == "MA1"


# --- patients: ambiguity is reported, never resolved ---


def _card(pid: int, born: str = "1971-04-12") -> dict:
    return {"id": pid, "vorname": "Anna", "nachname": "Weber", "geburtsdatum": born}


def test_two_matching_cards_are_ambiguous_and_bind_to_neither() -> None:
    """Spec §3.2 on the real calendar: the platform must not pick between two people."""
    calendar, _ = _calendar(patients=[_card(1), _card(2)])

    found = calendar.match_patients("Anna", "Weber", "1971-04-12")

    assert len(found) == 2
    assert calendar.find_patient(Patient(vorname="Anna", nachname="Weber", geburtsdatum="1971-04-12")) is None


def test_one_matching_card_is_reused() -> None:
    calendar, _ = _calendar(patients=[_card(7)])

    found = calendar.match_patients("Anna", "Weber", "1971-04-12")

    assert [c.id for c in found] == [7]


def test_a_different_birth_date_is_a_different_person() -> None:
    calendar, _ = _calendar(patients=[_card(7, born="1980-01-01")])

    assert calendar.match_patients("Anna", "Weber", "1971-04-12") == []


# --- the write lands in the room the caller's slot chose ---


def test_the_booking_is_written_into_the_room_the_slot_named() -> None:
    """Three equivalent calendars, one chosen slot. Writing into a fixed room would double-book."""
    day = _next_open()
    calendar, recorded = _calendar()

    calendar.create_appointment(
        Appointment(ref="voice-abc", start=f"{day}T10:00", raw={"resource": "MA3"}),
        patient_id=7,
    )

    written = recorded[0]["variables"]["input"]["terminInput"]
    assert written["mandantMitarbeiterId"] == ROOMS["MA3"]
    assert written["bemerkung"].endswith("voice-abc")
    # Never forced on a live call: bypassing thevea's own validation is how an appointment lands
    # in a room the practice is absent from (ADR-0005 D6).
    assert written["ignoreValidation"] is False


def test_a_resource_with_no_room_mapping_is_refused_rather_than_defaulted() -> None:
    day = _next_open()
    calendar, _ = _calendar()

    with pytest.raises(TheveaCalendarUnconfigured):
        calendar.create_appointment(
            Appointment(ref="voice-abc", start=f"{day}T10:00", raw={"resource": "MA9"}),
            patient_id=7,
        )


def test_existing_appointment_blocks_every_overlapping_slot() -> None:
    day = _next_open()
    calendar, _ = _calendar(termine=[{
        "id": room, "from": _utc_instant(f"{day}T09:15"),
        "until": _utc_instant(f"{day}T10:15"),
        "mandantMitarbeiterId": room,
    } for room in ROOMS.values()])
    slots = calendar.free_slots(
        date_from=day, date_to=day, limit=3,
        now=datetime.combine(day, datetime.min.time()),
    )
    assert slots[0].start == f"{day}T10:30"
    assert not calendar.is_free(f"{day}T09:30", "MA1")
    assert calendar.is_free(f"{day}T10:30", "MA1")


def test_patient_appointments_use_the_graphql_patient_id_field() -> None:
    day = _next_open()
    calendar, _ = _calendar(termine=[{
        "id": 42, "patientId": 7, "from": _utc_instant(f"{day}T09:00"),
        "until": _utc_instant(f"{day}T09:30"), "mandantMitarbeiterId": 101,
    }])
    assert [a.ref for a in calendar.appointments_for(7)] == ["42"]
    assert calendar.appointments_for(8) == []
    assert [a.ref for a in calendar.appointments_for(7, upcoming_only=False)] == ["42"]


def test_unknown_resource_is_never_free() -> None:
    calendar, _ = _calendar()
    assert not calendar.is_free(f"{_next_open()}T09:00", "MA9")


def test_unreadable_appointment_time_does_not_make_room_available() -> None:
    calendar, _ = _calendar(termine=[{
        "id": 42, "from": "bad-date", "until": "bad-date", "mandantMitarbeiterId": 101,
    }])
    with pytest.raises(TheveaError):
        calendar.is_free(f"{_next_open()}T09:00", "MA1")


# --- resolution: what a deployed instance actually gets ---


class _Conn:
    def __init__(self, adapter: str, config: dict | None = None) -> None:
        self.adapter, self.config = adapter, config or {}


def _resolve(monkeypatch, connection) -> tuple:
    import asyncio

    from app.workloads.conversational import calendar as resolver

    async def bound(_session, *, instance_id, role):
        return connection

    monkeypatch.setattr(resolver.repo, "instance_connection", bound)
    instance = type("I", (), {"id": "i"})()
    return asyncio.run(resolver.resolve_calendar(None, instance))


def test_the_studios_simulated_connection_rehearses_in_the_sandbox(monkeypatch) -> None:
    """Every instance deployed before a real practice calendar existed is bound to `memory`.

    It is a real Connection row, so it is not "unbound" — but it names no system of record, and
    rejecting it would break the voice test on every one of them.
    """
    from app.providers.sandbox import SandboxCalendar

    calendar, kind = _resolve(monkeypatch, _Conn("memory"))

    assert isinstance(calendar, SandboxCalendar)
    assert kind == "sandbox"


def test_an_unbound_calendar_role_also_rehearses(monkeypatch) -> None:
    from app.providers.sandbox import SandboxCalendar

    calendar, kind = _resolve(monkeypatch, None)

    assert isinstance(calendar, SandboxCalendar)
    assert kind == "sandbox"


def test_an_unsupported_adapter_is_refused_rather_than_quietly_downgraded(monkeypatch) -> None:
    """A real binding must never become a sandbox behind the caller's back (ADR-0003 D4)."""
    with pytest.raises(RuntimeError, match="no calendar for adapter"):
        _resolve(monkeypatch, _Conn("healthyfeet"))


def test_calendar_query_uses_practice_timezone_instead_of_server_timezone() -> None:
    from types import SimpleNamespace
    captured = {}

    def read(start, end, **kwargs):
        captured.update(start=start, end=end)
        return []

    calendar = TheveaPracticeCalendar(SimpleNamespace(termine_between=read), ROOMS)
    calendar.is_free("2026-09-14T09:00", "MA1")
    assert captured["start"].utcoffset() == timedelta(hours=2)
    assert captured["start"].hour == 9
    assert captured["end"].minute == 30


# --- two bugs in the first version of this adapter, kept honest by tests ---


def test_an_appointment_longer_than_one_slot_blocks_every_slot_it_covers() -> None:
    """The first version compared START times only.

    thevea holds real durations — a sixty minute appointment at 09:00 runs to 10:00 — and an
    availability read that only matched 09:00 would have offered 09:30 to the next caller and
    double-booked the room.
    """
    day = _next_open()
    long_one = [
        {
            "id": 1,
            "from": _utc_instant(f"{day}T09:00"),
            "until": _utc_instant(f"{day}T10:00"),
            "mandantMitarbeiterId": room,
            "status": "gebucht",
        }
        for room in ROOMS.values()
    ]
    calendar, _ = _calendar(termine=long_one)

    slots = calendar.free_slots(
        date_from=day, date_to=day, limit=3, now=datetime.combine(day, datetime.min.time())
    )

    offered = {s.start for s in slots}
    assert f"{day}T09:00" not in offered
    assert f"{day}T09:30" not in offered, "a 60-minute appointment must block its second half"
    assert f"{day}T10:00" in offered


def test_a_patients_appointments_are_found_by_the_field_the_read_query_returns() -> None:
    """`getTermine` returns `patientId`; `patientenId` is the CREATE mutation's input field.

    Reading the wrong one made `appointments_for` match nothing, so every caller looked like they
    had no appointments — and nothing could be verified, moved or cancelled.
    """
    day = _next_open()
    mine = [
        {
            "id": 55,
            "patientId": 7,
            "from": _utc_instant(f"{day}T09:00"),
            "until": _utc_instant(f"{day}T09:30"),
            "mandantMitarbeiterId": 101,
            "status": "gebucht",
        }
    ]
    calendar, _ = _calendar(termine=mine)

    found = calendar.appointments_for(7, now=datetime.combine(day, datetime.min.time()))

    assert [a.start for a in found] == [f"{day}T09:00"]
