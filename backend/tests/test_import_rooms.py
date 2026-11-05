"""Which rooms an import writes into, and which it searches for a copy it already made.

The practice stopped booking MA 1 and MA 4 with appointments still in them. New appointments go to
MA 2 and MA 3 only, but the idempotency check must keep looking in all four: one that stops looking
in MA 1 finds nothing there and imports every appointment already in it a second time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.connectors.base import Appointment
from app.sync import orchestrator

MA1, MA2, MA3, MA4 = 208413, 208416, 229566, 240570


class _Runtime:
    def __init__(self):
        self.cases: list[dict] = []

    async def run_instance(self, session, instance, case):
        self.cases.append(case)
        return SimpleNamespace(steps=[SimpleNamespace(status="ok", reason=None)])


class _Source:
    def __init__(self, appointments):
        self._appointments = appointments

    def list_appointments(self, window):
        return self._appointments

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
