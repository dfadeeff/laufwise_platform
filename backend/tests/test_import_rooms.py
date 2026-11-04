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


def _run(params, monkeypatch, n=3):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    appointments = [
        Appointment(ref=f"DL-{i}", start=start, patient="P", raw={"status": "confirmed"})
        for i in range(n)
    ]

    async def bound(session, instance, role, **opts):
        return _Source(appointments)

    monkeypatch.setattr(orchestrator, "_bound_connector", bound)
    monkeypatch.setattr(orchestrator, "_overall", lambda result: "ok")
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
