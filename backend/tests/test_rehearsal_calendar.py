"""A Studio test call can use the practice's real calendar: read-only, or writing test bookings.

Three modes for the browser test (ADR-0018). `sandbox` is what it always was. `read` reads the
real calendar and stops before any write, saying so. `write` books for real, only with an explicit
confirmation, and labels every appointment as a test so staff can find and delete it.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession


def _slot() -> str:
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return f"{day.isoformat()}T09:00"


def _ready(session: BookingSession) -> None:
    session.set_details(
        first_name="Test", last_name="Patient", date_of_birth="1971-04-12",
        phone="0176 4289 9911", service_key="medizinische_fusspflege", preferred_time=_slot(),
    )
    session.find_patient()
    session.confirm("Test Patient, neun Uhr.")


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def test_a_read_only_test_call_reads_everything_and_writes_nothing() -> None:
    session = BookingSession("read-test", test_mode="read")
    _ready(session)

    result = session.book()

    assert result["status"] == "not_written"
    assert "Nothing was booked" in " ".join(result["agent_notes"])
    assert session.calendar.appointments == []
    assert session.calendar.match_patients("Test", "Patient", "1971-04-12") == []


def test_a_read_only_test_still_says_which_detail_is_missing() -> None:
    """Short-circuiting the write must not skip the checks a real booking would fail."""
    session = BookingSession("read-test", test_mode="read")

    assert session.book()["status"] != "not_written"


def test_a_written_test_booking_is_labelled_so_staff_can_find_and_delete_it() -> None:
    session = BookingSession("write-test", test_mode="write")
    _ready(session)

    assert session.book()["status"] == "ok"
    (appointment,) = session.calendar.appointments
    assert appointment.raw["service_label"].startswith("TEST ·")


def test_an_ordinary_booking_carries_no_test_label() -> None:
    session = BookingSession("real")
    _ready(session)
    session.book()

    assert "service_label" not in session.calendar.appointments[0].raw


# --- the Studio asks for a mode, and the platform enforces it ----------------------------------


def test_writing_to_the_real_calendar_needs_an_explicit_confirmation() -> None:
    from pydantic import ValidationError

    from app.api.v1.conversational import StudioVoiceSessionRequest

    with pytest.raises(ValidationError, match="confirm"):
        StudioVoiceSessionRequest(agent_id="a", calendar_mode="write", connection_id="c")
    with pytest.raises(ValidationError, match="calendar account"):
        StudioVoiceSessionRequest(agent_id="a", calendar_mode="read")
    StudioVoiceSessionRequest(
        agent_id="a", calendar_mode="write", connection_id="c", confirm_real_writes=True
    )


def test_a_real_calendar_rehearsal_builds_the_system_s_calendar_and_its_limits(monkeypatch) -> None:
    from app.agents import runtime
    from app.workloads.conversational.calendar import VOICE_CALENDARS

    built: list = []
    system = VOICE_CALENDARS["doctolib"]
    monkeypatch.setitem(
        VOICE_CALENDARS,
        "doctolib",
        system.__class__(**{**system.__dict__, "build": lambda c, p: built.append(c) or "real"}),
    )
    connection = SimpleNamespace(adapter="doctolib")
    instance = SimpleNamespace(runtime_config=AgentConfig().model_dump())

    calendar, kind, config = asyncio.run(
        runtime.prepare_voice(None, instance, rehearsal=True, calendar_mode="read", connection=connection)
    )

    assert (calendar, kind) == ("real", "doctolib") and built == [connection]
    assert config.booking_enabled is False  # Doctolib cannot book, in a test too
