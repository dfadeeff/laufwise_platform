"""Which rooms an import writes into, and which it searches for a copy it already made.

The practice stopped booking MA 1 and MA 4 with appointments still in them. New appointments go to
MA 2 and MA 3 only, but the idempotency check must keep looking in all four: one that stops looking
in MA 1 finds nothing there and imports every appointment already in it a second time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.connectors.base import Appointment, BusyRange
from app.providers.thevea import TheveaError
from app.sync import orchestrator

MA1, MA2, MA3, MA4 = 208413, 208416, 229566, 240570


class _Runtime:
    def __init__(self):
        self.cases: list[dict] = []

    async def run_instance(self, session, instance, case):
        self.cases.append(case)
        return SimpleNamespace(steps=[SimpleNamespace(status="ok", reason=None)])


class _Source:
    """The source and the destination at once: the import reads appointments from it, and the
    placement ladder reads which thevea rooms are taken (`busy`, as BusyRanges)."""

    def __init__(self, appointments, busy=(), fail=False):
        self._appointments, self._busy, self._fail = appointments, list(busy), fail

    def list_appointments(self, window):
        return self._appointments

    def list_busy(self, day, rooms):
        if self._fail:
            raise TheveaError("down")
        return [b for b in self._busy if b.start.startswith(day)]

    def close(self):
        pass


def _run(params, monkeypatch, n=3, status="ok"):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    appointments = [
        Appointment(ref=f"DL-{i}", start=start, patient="P", raw={"status": "confirmed"})
        for i in range(n)
    ]

    async def bound(session, instance, role, **opts):
        return _Source(appointments)

    monkeypatch.setattr(orchestrator, "_bound_connector", bound)
    monkeypatch.setattr(orchestrator, "_overall", lambda result: status)
    runtime = _Runtime()
    instance = SimpleNamespace(param_values=params)
    asyncio.run(orchestrator.run_import(None, runtime, instance, {}))
    return runtime.cases


def test_writes_only_into_rooms_but_searches_every_room_for_an_existing_copy(monkeypatch):
    cases = _run(
        {"rooms": f"{MA2},{MA3}", "search_rooms": f"{MA1},{MA2},{MA3},{MA4}"}, monkeypatch
    )

    assert [c["room_id"] for c in cases] == [MA2, MA3, MA2]
    assert all(c["rooms"] == [MA2, MA3, MA1, MA4] for c in cases)


def test_an_instance_without_search_rooms_searches_where_it_writes(monkeypatch):
    cases = _run({"rooms": f"{MA1},{MA2}"}, monkeypatch, n=1)

    assert cases[0]["rooms"] == [MA1, MA2]


# The Studio carries an instance's old `rooms` value across a redeploy, so an instance set up
# before MA 1 and MA 4 closed keeps asking to write there. The server's list of writable rooms
# wins over whatever the instance asks for.


def test_an_old_instance_cannot_write_into_closed_rooms(monkeypatch):
    cases = _run({"rooms": f"{MA1},{MA2},{MA3},{MA4}"}, monkeypatch, n=4)

    assert [c["room_id"] for c in cases] == [MA2, MA3, MA2, MA3]


def test_closed_rooms_are_still_searched_for_a_copy_already_there(monkeypatch):
    cases = _run({"rooms": f"{MA1},{MA4}"}, monkeypatch, n=1)

    assert cases[0]["room_id"] in (MA2, MA3)
    assert set(cases[0]["rooms"]) == {MA1, MA2, MA3, MA4}


def test_an_instance_without_rooms_writes_into_the_open_rooms_not_ma1(monkeypatch):
    cases = _run({}, monkeypatch, n=2)

    assert [c["room_id"] for c in cases] == [MA2, MA3]


def test_no_retry_or_forced_attempt_lands_in_a_closed_room(monkeypatch):
    cases = _run({"rooms": f"{MA1},{MA2}"}, monkeypatch, n=1, status="rejected")

    assert {c["room_id"] for c in cases} == {MA2}


def test_an_empty_setting_lifts_the_restriction(monkeypatch):
    monkeypatch.setattr(orchestrator.settings, "import_write_rooms", "")

    cases = _run({"rooms": f"{MA1},{MA2}"}, monkeypatch, n=2)

    assert [c["room_id"] for c in cases] == [MA1, MA2]


# The practice pairs its doctolib rooms with thevea's: Behandlungsraum 1 is MA 2, Behandlungsraum 2
# is MA 3. An appointment booked in one of them goes into its pair, so the two calendars show the
# same room taken; one in no paired room is shared out between the open rooms as before.
RAUM1, RAUM2, PRACTITIONER = "2570190", "2557171", "2556024"


def _run_from(calendars, monkeypatch, status="ok", setting=f"{RAUM1}:{MA2},{RAUM2}:{MA3}",
              same_time=False, busy=(), fail=False):
    monkeypatch.setattr(orchestrator.settings, "import_room_by_calendar", setting)
    appointments = [
        Appointment(ref=f"DL-{i}", start=_at(0 if same_time else i).isoformat(), patient="P",
                    raw={"status": "confirmed", "source_calendars": c})
        for i, c in enumerate(calendars)
    ]

    async def bound(session, instance, role, **opts):
        return _Source(appointments, busy, fail)

    monkeypatch.setattr(orchestrator, "_bound_connector", bound)
    monkeypatch.setattr(orchestrator, "_overall", lambda result: status)
    runtime = _Runtime()
    asyncio.run(orchestrator.run_import(None, runtime, SimpleNamespace(param_values={}), {}))
    return runtime.cases


def test_an_appointment_goes_into_the_room_paired_with_its_doctolib_room(monkeypatch):
    cases = _run_from(
        [[PRACTITIONER, RAUM2], [PRACTITIONER, RAUM2], [PRACTITIONER, RAUM1], [RAUM1]], monkeypatch
    )

    assert [c["room_id"] for c in cases] == [MA3, MA3, MA2, MA2]


def test_one_in_no_paired_room_is_shared_out_as_before(monkeypatch):
    cases = _run_from([[PRACTITIONER], [RAUM2], [], [PRACTITIONER]], monkeypatch)

    assert [c["room_id"] for c in cases] == [MA2, MA3, MA3, MA2]


def test_a_paired_room_that_refuses_still_lets_another_room_take_it(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, status="rejected")

    assert [(c["room_id"], c["force"]) for c in cases] == [(MA2, False), (MA3, False), (MA2, True)]


def test_a_pair_into_a_room_the_server_does_not_write_is_ignored(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, setting=f"{RAUM1}:{MA1}")

    assert cases[0]["room_id"] == MA2


# A paired room that is already taken at that time is not the first choice any more: a free
# neighbour is, and two patients go into one room only when every room is taken (owner, 13.11.2026:
# two appointments landed in MA 2 at 17:00 while MA 3 stood empty).

def _at(hours):
    """Ten o'clock (UTC) three days from now, plus `hours` — each appointment its own slot."""
    day = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    return day + timedelta(hours=hours)


def _taken(room, hours=0):
    start = _at(hours)
    return BusyRange(room=str(room), start=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                     end=(start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def test_a_taken_paired_room_gives_way_to_a_free_neighbour(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, busy=[_taken(MA2)])

    assert [c["room_id"] for c in cases] == [MA3]


def test_a_free_paired_room_is_still_the_first_choice(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, busy=[_taken(MA3)])

    assert [c["room_id"] for c in cases] == [MA2]


def test_with_every_room_taken_it_goes_into_the_paired_room(monkeypatch):
    cases = _run_from([[RAUM2]], monkeypatch, busy=[_taken(MA2), _taken(MA3)])

    assert [(c["room_id"], c["force"]) for c in cases] == [(MA3, False)]


def test_an_appointment_placed_in_this_run_takes_its_room(monkeypatch):
    cases = _run_from([[RAUM1], [RAUM1], [RAUM1]], monkeypatch, same_time=True)

    assert [c["room_id"] for c in cases] == [MA2, MA3, MA2]


def test_a_neighbours_appointment_at_another_time_does_not_count(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, busy=[_taken(MA2, hours=1)])

    assert [c["room_id"] for c in cases] == [MA2]


def test_an_unreadable_thevea_keeps_the_paired_order(monkeypatch):
    cases = _run_from([[RAUM1]], monkeypatch, busy=[_taken(MA2)], fail=True)

    assert [c["room_id"] for c in cases] == [MA2]
