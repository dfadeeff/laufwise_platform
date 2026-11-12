"""The doctolib block mirror (ADR-0044): thevea's taken times become blocks in doctolib.

What can go wrong silently, and is pinned here: a block over a doctolib booking (it may suspend
it), a block someone set by hand being touched, a time that moves being open in between, the
practice's day read in UTC, and a write believed on its own word.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from laufwise.state.base import StateUnavailable

from app.connectors.base import Block, BusyRange
from app.providers.blocks import NOTES, TITLE, BlocksDayProvider, plan_day, wanted
from app.providers.doctolib import DoctolibConnector, DoctolibError
from app.providers.thevea import TheveaError
from app.sync.block_mirror import describe, pairs_param
from app.workloads.block_tools import block_tools

MA2, MA3 = 208416, 229566
BR1, BR2 = "2570190", "2557171"
PAIRS = {MA2: BR1, MA3: BR2}
DAY = "2027-03-01"  # winter time: Berlin is UTC+1
EARLY = datetime(2027, 3, 1, 0, 0, tzinfo=timezone.utc)


def busy(room, start, end):
    return BusyRange(room=str(room), start=f"{DAY}T{start}:00.000Z", end=f"{DAY}T{end}:00.000Z")


def block(agenda, start, end):
    return Block(agenda, f"{DAY}T{start}:00.000Z", f"{DAY}T{end}:00.000Z")


# --- what each agenda should block ------------------------------------------------------------

def test_touching_times_become_one_block_per_agenda():
    ranges = [busy(MA2, "08:00", "08:30"), busy(MA2, "08:30", "09:00"), busy(MA2, "10:00", "10:30"),
              busy(MA3, "08:00", "09:00")]
    assert wanted(ranges, [], PAIRS) == sorted([
        block(BR1, "08:00", "09:00"), block(BR1, "10:00", "10:30"), block(BR2, "08:00", "09:00"),
    ])


def test_a_block_leaves_every_doctolib_booking_open():
    """A block over an appointment may suspend it: the booking itself is cut out of the block."""
    ranges = [busy(MA2, "08:00", "11:00")]
    taken = [block(BR1, "09:00", "09:30"), block(BR2, "10:00", "10:30")]  # BR2 is not MA2's agenda
    assert wanted(ranges, taken, PAIRS) == [block(BR1, "08:00", "09:00"), block(BR1, "09:30", "11:00")]


def test_a_room_outside_the_pairs_blocks_nothing():
    assert wanted([busy(208413, "08:00", "09:00")], [], PAIRS) == []


def test_an_absent_day_is_blocked_to_the_whole_minute():
    """thevea ends an absent day at 23:59:59 Berlin; doctolib keeps minutes, and a block that can
    never be read back the way it was written would be rewritten on every run."""
    absent = BusyRange(str(MA3), "2027-02-28T23:00:00.000Z", "2027-03-01T22:59:59.000Z")
    assert wanted([absent], [], PAIRS) == [Block(BR2, "2027-02-28T23:00:00.000Z", "2027-03-01T23:00:00.000Z")]


# --- planning against what is there ------------------------------------------------------------

class _Occupancy:
    def __init__(self, ranges, fail=False):
        self.ranges, self.fail, self.asked = ranges, fail, []

    def list_busy(self, day, rooms):
        self.asked.append((day, sorted(rooms)))
        if self.fail:
            raise TheveaError("down")
        return list(self.ranges)


class _Blocks:
    """doctolib as the mirror sees it: bookings per agenda, and blocks by id."""

    def __init__(self, taken=(), events=None, fail_create=False):
        self.taken_list, self.events = list(taken), dict(events or {})
        self.fail_create, self.log, self._next = fail_create, [], 100

    def taken(self, day, agendas):
        return [t for t in self.taken_list if t.agenda in agendas]

    def get_block(self, event_id):
        return self.events.get(event_id)

    def create_block(self, b, title, notes):
        if self.fail_create:
            raise DoctolibError("422")
        self._next += 1
        self.events[str(self._next)] = b
        self.log.append(("create", b, title, notes))
        return str(self._next)

    def delete_block(self, event_id, b):
        self.events.pop(event_id, None)
        self.log.append(("delete", event_id))


class _Ledger:
    def __init__(self, owned=()):
        self.rows = dict(owned)

    def owned(self, day):
        return list(self.rows.items())

    def record(self, day, event_id, b):
        self.rows[event_id] = b

    def forget(self, event_id):
        self.rows.pop(event_id, None)


def test_plan_creates_what_is_missing_and_deletes_only_its_own_stale_blocks():
    ledger = _Ledger({"1": block(BR1, "08:00", "09:00"), "2": block(BR1, "12:00", "13:00"), "3": block(BR2, "08:00", "09:00")})
    # "3" was deleted in doctolib by someone; "9" is a block set by hand, never in the ledger.
    doctolib = _Blocks(events={"1": block(BR1, "08:00", "09:00"), "2": block(BR1, "12:00", "13:00"),
                               "9": block(BR1, "14:00", "15:00")})
    occupancy = _Occupancy([busy(MA2, "08:00", "09:00"), busy(MA3, "10:00", "11:00")])

    plan = plan_day(occupancy, doctolib, ledger, DAY, PAIRS, now=EARLY)

    assert plan.create == [block(BR2, "10:00", "11:00")]
    assert plan.delete == [("2", block(BR1, "12:00", "13:00"))]
    assert plan.gone == ["3"]
    assert occupancy.asked == [(DAY, [MA2, MA3])]


def test_the_past_is_left_as_it_is():
    ledger = _Ledger({"1": block(BR1, "07:00", "08:00")})
    doctolib = _Blocks(events={"1": block(BR1, "07:00", "08:00")})
    occupancy = _Occupancy([busy(MA2, "06:00", "07:00"), busy(MA2, "15:00", "16:00")])
    noon = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)

    plan = plan_day(occupancy, doctolib, ledger, DAY, PAIRS, now=noon)

    assert plan.create == [block(BR1, "15:00", "16:00")] and plan.delete == []


def test_the_tool_creates_first_records_each_id_and_then_agrees():
    ledger = _Ledger({"1": block(BR1, "08:00", "08:30")})
    doctolib = _Blocks(events={"1": block(BR1, "08:00", "08:30")})
    occupancy = _Occupancy([busy(MA2, "08:00", "09:00")])  # the appointment grew by half an hour

    outcome = block_tools(occupancy, doctolib, ledger, DAY, PAIRS)["sync_blocks_day"](None, None)

    assert outcome.ok
    # The longer block is set before the shorter one goes, so the time is never open in between.
    assert [entry[0] for entry in doctolib.log] == ["create", "delete"]
    assert doctolib.log[0][2:] == ("", "")  # a plain Abwesenheit: no label, no note
    assert list(ledger.rows.values()) == [block(BR1, "08:00", "09:00")]
    view = BlocksDayProvider(occupancy, doctolib, ledger, DAY, PAIRS).query("day_matches_occupancy")
    assert view.value["in_sync"] is True


def test_a_failed_write_is_reported_not_claimed():
    tool = block_tools(_Occupancy([busy(MA2, "08:00", "09:00")]), _Blocks(fail_create=True),
                       _Ledger(), DAY, PAIRS)["sync_blocks_day"]
    outcome = tool(None, None)
    assert not outcome.ok and "0 created" in outcome.note


def test_an_unreadable_system_halts_the_day():
    provider = BlocksDayProvider(_Occupancy([], fail=True), _Blocks(), _Ledger(), DAY, PAIRS)
    with pytest.raises(StateUnavailable):
        provider.query("day_matches_occupancy")


# --- the doctolib connector's block calls ---------------------------------------------------

def _doctolib(handler):
    return DoctolibConnector(
        "https://pro.doctolib.de", "u", "p", session_cookie="s", transport=httpx.MockTransport(handler)
    )


def test_create_block_sends_berlin_times_and_the_pages_csrf_token():
    seen = []

    def handler(request):
        if request.url.path == "/calendar":
            return httpx.Response(200, text='<meta name="csrf-token" content="tok-1" />')
        seen.append(request)
        return httpx.Response(200, json={"id": 1306850467, "kind": "blck"})

    event_id = _doctolib(handler).create_block(block(BR1, "07:00", "07:30"), TITLE, NOTES)

    assert event_id == "1306850467"
    body = json.loads(seen[0].content)
    assert (seen[0].method, seen[0].url.path, seen[0].headers["X-CSRF-Token"]) == ("POST", "/api/events", "tok-1")
    assert (body["start_date"], body["end_date"]) == ("2027-03-01 08:00:00.000", "2027-03-01 08:30:00.000")
    assert (body["agenda_ids"], body["type"], body["recurring"], body["title"]) == ([2570190], "blck", False, TITLE)


def test_delete_block_names_the_one_occurrence():
    seen = []

    def handler(request):
        if request.url.path == "/calendar":
            return httpx.Response(200, text="")
        seen.append(request)
        return httpx.Response(204)

    _doctolib(handler).delete_block("77", block(BR1, "07:00", "07:30"))

    params = dict(seen[0].url.params)
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/api/events/77")
    assert params == {"until": "2027-02-28T23:00:00.000Z", "edition_mode": "occurrence",
                      "type": "blck", "date": "2027-03-01 08:00:00.000"}


def test_get_block_reads_a_deleted_or_missing_block_as_gone():
    rows = {
        "/api/events/1": httpx.Response(200, json={"id": 1, "agenda_id": 2570190, "deleted_at": None,
                                                   "start_date": "2027-03-01T08:00:00.000+01:00",
                                                   "end_date": "2027-03-01T08:30:00.000+01:00"}),
        "/api/events/2": httpx.Response(200, json={"id": 2, "agenda_id": 2570190, "status": "deleted",
                                                   "deleted_at": "2027-03-01T09:00:00.000+01:00",
                                                   "start_date": "2027-03-01T08:00:00.000+01:00",
                                                   "end_date": "2027-03-01T08:30:00.000+01:00"}),
        "/api/events/3": httpx.Response(404, json={"status": 404}),
    }
    doctolib = _doctolib(lambda request: rows[request.url.path])

    assert doctolib.get_block("1") == block(BR1, "07:00", "07:30")
    assert doctolib.get_block("2") is None
    assert doctolib.get_block("3") is None


def test_taken_counts_only_bookings_that_still_hold_their_time():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"id": "a", "status": "confirmed", "start_date": "2027-03-01T09:00:00.000+01:00",
             "end_date": "2027-03-01T09:30:00.000+01:00"},
            {"id": "b", "status": "deleted", "start_date": "2027-03-01T10:00:00.000+01:00",
             "end_date": "2027-03-01T10:30:00.000+01:00"},
        ]})

    assert _doctolib(handler).taken(DAY, [BR1]) == [block(BR1, "08:00", "08:30")]


def test_a_refused_write_is_an_error():
    def handler(request):
        return httpx.Response(200, text="") if request.url.path == "/calendar" else httpx.Response(422)

    with pytest.raises(DoctolibError):
        _doctolib(handler).create_block(block(BR1, "07:00", "07:30"), TITLE, NOTES)


# --- the instance's settings and the preview's words ------------------------------------------

def test_pairs_param_reads_room_agenda_pairs_and_drops_the_rest():
    assert pairs_param({"pairs": "208416:2570190, 229566:2557171,bad,1:"}) == PAIRS
    assert pairs_param({}) == {}


def test_describe_says_what_a_day_would_get_in_berlin_time():
    from app.providers.blocks import Plan

    plan = Plan(create=[block(BR1, "07:00", "08:30")], delete=[("5", block(BR2, "13:00", "14:00"))])
    assert describe(plan) == "would set and remove: + 2570190 08:00–09:30; − 2557171 14:00–15:00"


# --- the governed contract, end to end on the engine -----------------------------------------

def _run_contract(tmp_path, occupancy, doctolib, ledger):
    from pathlib import Path

    from app.control_plane.runner import execute_contract
    from app.templates.loader import load_template

    contract = load_template(Path(__file__).parents[1] / "runbooks" / "doctolib_block_mirror.yaml")
    return execute_contract(
        contract, {"day": DAY}, tmp_path,
        real_providers={"blocks": BlocksDayProvider(occupancy, doctolib, ledger, DAY, PAIRS)},
        extra_tools=block_tools(occupancy, doctolib, ledger, DAY, PAIRS),
    )


def _status(result):
    return [s.status.value if hasattr(s.status, "value") else s.status for s in result.steps]


def test_the_contract_writes_a_day_once_and_then_skips_it(tmp_path):
    occupancy = _Occupancy([busy(MA2, "08:00", "09:00"), busy(MA3, "10:00", "11:00")])
    doctolib, ledger = _Blocks(), _Ledger()

    first = _run_contract(tmp_path, occupancy, doctolib, ledger)
    again = _run_contract(tmp_path, occupancy, doctolib, ledger)

    assert _status(first) == ["ok"]
    assert sorted(doctolib.events.values()) == sorted([block(BR1, "08:00", "09:00"), block(BR2, "10:00", "11:00")])
    assert _status(again) == ["blocked"]  # already in sync: a governed skip, no second write
    assert [e[0] for e in doctolib.log] == ["create", "create"]


def test_the_contract_rejects_a_write_doctolib_did_not_keep(tmp_path):
    class _Forgetful(_Blocks):
        def get_block(self, event_id):
            return None  # accepted, then not there

    result = _run_contract(tmp_path, _Occupancy([busy(MA2, "08:00", "09:00")]), _Forgetful(), _Ledger())

    assert _status(result) == ["rejected"]
