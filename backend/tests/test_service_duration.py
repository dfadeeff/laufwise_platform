"""Every appointment takes its treatment's own length (Mia spec v3 #13, #140).

Until now every booking was one 30-minute slot, whatever the treatment. A 60-minute treatment was
offered at 11:30 (running into the 12:00 break), at 9:00 when 9:30 was already taken, and written
to the calendar as 30 minutes. The length now comes from the service catalogue and is checked by
the offer, by the precondition at the write, and written as the appointment's end.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app.agents.config import AgentConfig
from app.connectors.base import Appointment
from app.providers.derived_availability import DerivedAvailability
from app.providers.sandbox import BOOKED, SandboxCalendar
from app.providers.thevea_calendar import TheveaPracticeCalendar
from app.workloads.conversational.booking import BookingSession

_CONFIG = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
    treatments=[
        {"key": "gross", "name": "Medizinische Fußpflege groß", "external_id": "1", "duration_minutes": 60},
        {"key": "klein", "name": "Medizinische Fußpflege klein", "external_id": "2"},
    ],
)


def _practice():
    return AgentConfig(**_CONFIG).to_practice()


def _monday() -> date:
    day = date.today() + timedelta(days=14)
    while day.weekday() != 0:
        day += timedelta(days=1)
    return day


def _at(day: date, hh: int, mm: int = 0) -> str:
    return datetime.combine(day, time(hh, mm)).strftime("%Y-%m-%dT%H:%M")


def _taken(calendar: SandboxCalendar, ref: str, start: str, resource: str) -> None:
    calendar.create_appointment(
        Appointment(ref=ref, start=start, type="x", raw={"resource": resource}), patient_id=1
    )
    assert calendar.find_appointment(ref).raw["status"] == BOOKED


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


# --- the grid has room for the whole treatment -----------------------------------------------------


def test_a_long_treatment_never_runs_into_the_break_or_past_closing() -> None:
    schedule = _practice().schedule
    starts = {s.strftime("%H:%M") for s in schedule.starts_on(_monday(), 60)}

    assert "11:00" in starts and "11:30" not in starts  # 11:30 + 60 min crosses 12:00
    assert "17:00" in starts and "17:30" not in starts  # past 18:00
    assert "11:30" in {s.strftime("%H:%M") for s in schedule.starts_on(_monday())}


def test_a_long_treatment_is_offered_only_where_all_of_it_is_free() -> None:
    practice = _practice()
    calendar = SandboxCalendar(practice=practice)
    day = _monday()
    for room in practice.schedule.resources:
        _taken(calendar, f"r-{room}", _at(day, 9, 30), room)

    long_ = calendar.free_slots(date_from=day, date_to=day, limit=1, minutes=60)
    short = calendar.free_slots(date_from=day, date_to=day, limit=1)

    assert short[0].start == _at(day, 9)
    assert long_[0].start == _at(day, 10)
    assert long_[0].end == _at(day, 11)


def test_a_system_that_reports_bookings_checks_the_whole_length_too() -> None:
    practice = _practice()
    day = _monday()

    class _Busy(DerivedAvailability):
        _practice = practice
        _resources = practice.schedule.resources

        def _occupied(self, start, end):
            return [
                (room, datetime.combine(day, time(9, 30)), datetime.combine(day, time(10)))
                for room in practice.schedule.resources
            ]

    calendar = _Busy()

    assert calendar.free_slots(date_from=day, date_to=day, limit=1, minutes=60)[0].start == _at(day, 10)
    assert calendar.any_resource_free(_at(day, 9), 60) is None
    assert calendar.any_resource_free(_at(day, 9)) is not None
    assert not calendar.in_grid(_at(day, 11, 30), 60)


# --- the booking uses the chosen treatment's length ------------------------------------------------


def _session(service: str, start: str) -> BookingSession:
    session = BookingSession("dur", practice=_practice())
    session.set_details(
        first_name="Max", last_name="Buchmann", date_of_birth="1995-01-05",
        phone="015159730614", service_key=service, preferred_time=start,
    )
    session.find_patient()
    summary = (
        f"Max Buchmann, {_practice().service(service).name}, am Montag in der Baumkirchner "
        "Straße 19. Ist das alles richtig?"
    )
    session.hear("agent", summary)
    session.hear("caller", "Ja, passt.")
    assert session.confirm(summary)["confirmed"] is True
    return session


def test_the_appointment_is_written_with_the_treatments_length() -> None:
    day = _monday()
    session = _session("gross", _at(day, 9))

    assert session.book()["status"] == "ok"
    written = session._calendar.find_appointment(session.booked_ref)
    assert (written.start, written.end) == (_at(day, 9), _at(day, 10))


def test_a_long_treatment_at_11_30_is_refused_as_off_the_grid() -> None:
    session = BookingSession("dur", practice=_practice())
    session.set_details(service_key="gross")

    result = session.set_details(preferred_time=_at(_monday(), 11, 30))

    assert "takes 60 minutes" in result["rejected"]["preferred_time"]
    assert session._draft["preferred_time"] == ""


def test_the_write_is_rechecked_on_the_calendar_it_goes_to() -> None:
    """Another calendar being free is no reason to write into a taken one."""
    day = _monday()
    session = _session("gross", _at(day, 9))
    chosen = session._draft["resource"]
    _taken(session._calendar, "later", _at(day, 9, 30), chosen)  # someone took its second half

    result = session.book()

    assert result["status"] == "blocked"
    assert "taken" in result["reason"]


# --- what reaches thevea -------------------------------------------------------------------------


def test_thevea_gets_the_treatments_length() -> None:
    written: dict = {}

    class _Connector:
        def create_appointment_in_room(self, appt, *, patient_id, room_id, minutes=None):
            written.update(room_id=room_id, minutes=minutes)

    calendar = TheveaPracticeCalendar(_Connector(), {"MA1": 1, "MA2": 2, "MA3": 3}, practice=_practice())
    day = _monday()

    calendar.create_appointment(
        Appointment(ref="r", start=_at(day, 9), end=_at(day, 10), type="x", raw={"resource": "MA2"}),
        patient_id=7,
    )

    assert written == {"room_id": 2, "minutes": 60}


def test_the_prompt_says_which_treatments_take_longer() -> None:
    block = _practice().knowledge_block()

    assert "Medizinische Fußpflege groß (60 min)" in block
    assert "Medizinische Fußpflege klein:" in block
