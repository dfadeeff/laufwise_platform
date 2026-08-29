"""The Studio voice agent books through the governed loop, and cannot book without the three
details it is required to collect.

These test the guarantee rather than the prompt. The prompt asks for a first name, a last name and
a preferred time; these prove that an agent which forgets to ask — or decides to be helpful and
skip one — still cannot produce an appointment.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from laufwise.adapters.base import StepOutcome

from app.connectors.base import Appointment, DestinationCalendar
from app.control_plane.runner import execute_contract
from app.providers.sandbox import SandboxCalendar, SandboxStateProvider
from app.templates.loader import load_template
from app.workloads.conversational.booking import (
    CONTRACT_PATH,
    OFFERED_SLOTS,
    BookingSession,
)
from app.workloads.conversational.surface import _booking_tools, _instructions

COMPLETE = {"first_name": "Anna", "last_name": "Weber", "preferred_time": "2026-09-03T11:00"}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BookingSession:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    return BookingSession("test-session")


@pytest.mark.parametrize(
    ("withheld", "named"),
    [
        ("first_name", "first name"),
        ("last_name", "last name"),
        ("preferred_time", "preferred appointment time"),
    ],
)
def test_booking_blocks_until_every_required_detail_is_collected(
    session: BookingSession, withheld: str, named: str
) -> None:
    """Each missing detail blocks the write AND names itself, so the agent knows what to ask."""
    session.set_details(**{k: v for k, v in COMPLETE.items() if k != withheld})

    result = session.book()

    assert result["status"] == "blocked"
    assert named in result["reason"]
    assert result["missing"] == [withheld]
    assert result["appointment"] is None


def test_complete_details_book_and_are_confirmed_by_the_calendar(session: BookingSession) -> None:
    session.set_details(**COMPLETE)

    result = session.book()

    assert result["status"] == "ok"
    assert result["appointment"] == COMPLETE


def test_a_correction_replaces_the_earlier_answer(session: BookingSession) -> None:
    """"Update" for a conversational agent means the draft, which stays mutable until it books."""
    session.set_details(first_name="Anna", last_name="Weber", preferred_time="2026-09-03T11:00")

    state = session.set_details(preferred_time="2026-09-04T09:30")

    assert state["collected"]["preferred_time"] == "2026-09-04T09:30"
    assert state["missing"] == []


def test_an_unresolvable_spoken_time_is_refused_rather_than_stored(
    session: BookingSession,
) -> None:
    """A phrase the model did not resolve must not become a booking at a time nobody chose."""
    state = session.set_details(preferred_time="sometime next week maybe")

    assert "preferred_time" in state["rejected"]
    assert state["collected"]["preferred_time"] == ""
    assert "preferred_time" in state["missing"]


def test_booking_the_same_details_twice_creates_one_appointment(session: BookingSession) -> None:
    """A dropped connection or a caller asking "did that go through?" must not double-book."""
    session.set_details(**COMPLETE)

    first, second = session.book(), session.book()

    assert (first["status"], second["status"]) == ("ok", "ok")
    assert len(session.calendar.appointments) == 1


def test_a_taken_slot_blocks_a_different_caller(session: BookingSession) -> None:
    session.set_details(**COMPLETE)
    session.book()

    session.set_details(first_name="Jonas", last_name="Klein")
    result = session.book()

    assert result["status"] == "blocked"
    assert "already taken" in result["reason"]


def test_a_tool_that_claims_success_without_writing_is_rejected(tmp_path: Path) -> None:
    """The governance property the whole design rests on.

    The postcondition re-queries the calendar, so the tool's own return value cannot decide the
    outcome. Without this, an agent could tell a caller their appointment is booked on the
    strength of nothing at all.
    """
    calendar = SandboxCalendar()

    result = execute_contract(
        load_template(CONTRACT_PATH),
        case={},
        runs_dir=tmp_path,
        real_providers={"sandbox": SandboxStateProvider(calendar, COMPLETE, "ref-1")},
        extra_tools={"book_appointment": lambda provider, step: StepOutcome(ok=True, note="lied")},
    )

    assert result.status == "rejected"
    assert "did not record the appointment" in result.steps[0].reason
    assert calendar.find_appointment("ref-1") is None


def _next_weekday(offset_days: int = 1) -> date:
    """The soonest open day at least `offset_days` out, so the suite never asks about a weekend."""
    day = date.today() + timedelta(days=offset_days)
    while day.weekday() not in SandboxCalendar.OPEN_WEEKDAYS:
        day += timedelta(days=1)
    return day


def test_free_slots_are_offered_and_capped(session: BookingSession) -> None:
    result = session.find_slots(_next_weekday().isoformat())

    assert len(result["slots"]) == OFFERED_SLOTS
    assert "reason" not in result


def test_a_booked_slot_stops_being_offered(session: BookingSession) -> None:
    """The point of the tool: what it returns has to track what the calendar actually holds."""
    day = _next_weekday()
    taken = session.find_slots(day.isoformat())["slots"][0]
    session.set_details(first_name="Anna", last_name="Weber", preferred_time=taken)
    assert session.book()["status"] == "ok"

    assert taken not in session.find_slots(day.isoformat())["slots"]


@pytest.mark.parametrize(
    ("day", "reason"),
    [
        ("2020-01-02", "already passed"),
        ("not a day", "not a date"),
    ],
)
def test_an_unofferable_day_says_why_rather_than_returning_a_bare_empty_list(
    session: BookingSession, day: str, reason: str
) -> None:
    """Closed, past and full are three different sentences; an empty list alone invites a guess."""
    result = session.find_slots(day)

    assert result["slots"] == []
    assert reason in result["reason"]


def test_a_closed_day_is_named_as_closed(session: BookingSession) -> None:
    saturday = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)

    result = session.find_slots(saturday.isoformat())

    assert result["slots"] == []
    assert "closed" in result["reason"]


def test_past_slots_are_never_offered_for_today() -> None:
    """A time the caller cannot take is a false offer, not a harmless one."""
    calendar = SandboxCalendar()
    midday = datetime.combine(_next_weekday(), datetime.min.time()).replace(hour=12, minute=15)

    slots = calendar.free_slots(midday.date(), limit=OFFERED_SLOTS, now=midday)

    assert slots and all(slot > midday.strftime("%Y-%m-%dT%H:%M") for slot in slots)


def test_an_offer_is_not_a_reservation(session: BookingSession, tmp_path: Path) -> None:
    """Availability is read outside the governed loop, so the slot is re-checked when it books.

    Between offering a time and booking it, someone else can take it. The precondition is what
    catches that — the offer itself reserves nothing.
    """
    day = _next_weekday()
    offered = session.find_slots(day.isoformat())["slots"][0]
    session.calendar.create_appointment(
        Appointment(ref="someone-else", start=offered), patient_id=1
    )

    session.set_details(first_name="Anna", last_name="Weber", preferred_time=offered)
    result = session.book()

    assert result["status"] == "blocked"
    assert "already taken" in result["reason"]


def test_the_calendar_seam_offers_no_way_to_change_a_booked_appointment() -> None:
    """Append-only is enforced by absence (ADR-0004 D7): asserted here so it stays absent."""
    assert isinstance(SandboxCalendar(), DestinationCalendar)
    for forbidden in ("update_appointment", "delete_appointment", "cancel_appointment"):
        assert not hasattr(SandboxCalendar, forbidden)


def test_the_prompt_asks_for_exactly_the_details_the_contract_enforces() -> None:
    """The prompt is the hint and the contract is the guarantee; they must at least agree."""
    prompt = _instructions("de")

    assert "{{" not in prompt
    for detail in ("first name", "last name", "preferred time"):
        assert detail in prompt.lower()


def test_every_registered_tool_is_described_in_the_prompt() -> None:
    """A tool the instructions never mention is a tool the agent will not reach for.

    Catches the two halves drifting apart — a tool added, renamed or removed on one side only.
    """
    prompt = _instructions("de")

    for tool in _booking_tools(BookingSession("test-session")):
        assert tool.to_default_dict()["name"] in prompt
