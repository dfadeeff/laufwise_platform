"""The occupancy mirror (ADR-0009) — the reverse direction, driven against mock transports.

Covers the three places this can go wrong silently: which entries count as "room taken", which
day is read (Berlin, not UTC — and it moves with summer/winter time), and whether the website is
believed about its own state (it is not: the provider compares both live reads).
"""

from __future__ import annotations

import json

import httpx
import pytest

from laufwise.state.base import StateUnavailable

from app.connectors.base import BusyRange, busy_digest
from app.providers.healthyfeet import HealthyfeetConnector, SourceError
from app.providers.occupancy import MirrorDayProvider
from app.providers.thevea import TheveaConnector, TheveaError
from app.workloads.mirror_tools import mirror_tools

ROOMS = [208413, 208416, 229566]


def _thevea(handler):
    return TheveaConnector(
        "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler)
    )


def _site(handler):
    return HealthyfeetConnector(
        "https://site/api/admin", "u", "p", transport=httpx.MockTransport(handler)
    )


def _termine_handler(termine, seen=None, absences=None):
    """thevea answers with two lists, not one: an absence is not a `Termin` (ADR-0011)."""

    def handler(request):
        body = json.loads(request.content)
        if "getTermine" in body.get("query", ""):
            if seen is not None:
                seen.update(body["variables"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "termine": termine,
                        "mitarbeiterAbwesenheitenFuerZeitraum": list(absences or []),
                    }
                },
            )
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    return handler


def _absence(room, day_from, day_until):
    """thevea's shape: dates, inclusive at both ends, and no reason — we never ask for one."""
    return {"id": 77, "from": day_from, "until": day_until, "personId": room}


# --- reading the practice calendar --------------------------------------------------------

def test_list_busy_keeps_appointments_and_drops_everything_else():
    """A room counts as taken by an appointment. An entry type we have not seen, and any room
    outside the website's own, must not close a slot: the allowlist stays an allowlist."""
    termine = [
        {"__typename": "PatientenTermin", "id": 1, "from": "2026-09-14T07:00:00.000Z",
         "until": "2026-09-14T07:30:00.000Z", "bemerkung": "Nagel · +49 · HF-260911-AB12",
         "mandantMitarbeiterId": 208413},
        {"__typename": "SonstigerTermin", "id": 2, "from": "2026-09-14T08:00:00.000Z",
         "until": "2026-09-14T09:00:00.000Z", "bemerkung": "", "mandantMitarbeiterId": 208416},
        {"__typename": "Terminblocker", "id": 3, "from": "2026-09-14T10:00:00.000Z",
         "until": "2026-09-14T12:00:00.000Z", "bemerkung": "",
         "mandantMitarbeiterId": 229566},
        {"__typename": "PatientenTermin", "id": 4, "from": "2026-09-14T10:00:00.000Z",
         "until": "2026-09-14T10:30:00.000Z", "bemerkung": "", "mandantMitarbeiterId": 999999},
    ]
    busy = _thevea(_termine_handler(termine)).list_busy("2026-09-14", ROOMS)

    assert [(b.room, b.start, b.end, b.site_ref) for b in busy] == [
        ("208413", "2026-09-14T07:00:00.000Z", "2026-09-14T07:30:00.000Z", "HF-260911-AB12"),
        ("208416", "2026-09-14T08:00:00.000Z", "2026-09-14T09:00:00.000Z", None),
    ]


def test_an_absent_room_is_taken_for_the_whole_day():
    """A holiday or a training day closes the room for every slot of the day, not for a time
    range: thevea states an absence in whole days, so half a day is not a thing it can mean.

    The published range therefore spans the practice day, and carries no site ref — nobody booked
    it on the website.
    """
    absences = [_absence(229566, "2026-09-14", "2026-09-18")]
    busy = _thevea(_termine_handler([], absences=absences)).list_busy("2026-09-16", ROOMS)

    assert [(b.room, b.site_ref) for b in busy] == [("229566", None)]
    # 00:00 Berlin to one second before midnight, i.e. 22:00 UTC the previous day in summer time.
    assert (busy[0].start, busy[0].end) == (
        "2026-09-15T22:00:00.000Z",
        "2026-09-16T21:59:59.000Z",
    )


def test_an_absence_closes_its_last_day_and_not_the_day_after():
    """thevea's `until` is INCLUSIVE — `14.09 → 18.09` is the Monday-to-Friday week the practice
    had also blocked by hand on the website. Reading it as exclusive would sell the Friday."""
    absences = [_absence(208413, "2026-09-14", "2026-09-18")]

    def rooms_closed(day):
        return [b.room for b in _thevea(_termine_handler([], absences=absences)).list_busy(day, ROOMS)]

    assert rooms_closed("2026-09-14") == ["208413"]   # first day
    assert rooms_closed("2026-09-18") == ["208413"]   # last day, inclusive
    assert rooms_closed("2026-09-21") == []           # the Monday after


def test_two_absences_on_one_room_publish_one_range():
    """Overlapping absences on the same room are still ONE closed room. A duplicate range would
    make the published day differ from the day read back, and the mirror would re-publish for
    ever — the idempotent skip (ADR-0009) depends on the two digests being able to agree."""
    absences = [_absence(208416, "2026-09-14", "2026-09-18"),
                _absence(208416, "2026-09-16", "2026-09-16")]
    busy = _thevea(_termine_handler([], absences=absences)).list_busy("2026-09-16", ROOMS)

    assert [b.room for b in busy] == ["208416"]


def test_an_absence_in_a_room_the_website_does_not_sell_is_ignored():
    """Augsburg's practitioners are in the same account. Their holidays are none of the website's
    business, and closing a Munich place because one of them is away would be a silent loss."""
    busy = _thevea(
        _termine_handler([], absences=[_absence(76050, "2026-09-14", "2026-09-18")])
    ).list_busy("2026-09-16", ROOMS)

    assert busy == []


def test_list_busy_frees_a_room_whose_appointment_was_cancelled():
    """thevea keeps a cancelled appointment and marks it `status: "ABGESAGT"`. It no longer holds
    its room, so the website must be able to offer that time again — otherwise the mirror closes
    slots the practice has already freed, which is the inverse of what it exists for.

    An UNKNOWN status keeps occupying: freeing a room on a guess would double-book a patient.
    """
    termine = [
        {"__typename": "PatientenTermin", "id": 1, "from": "2026-09-14T07:00:00.000Z",
         "until": "2026-09-14T07:30:00.000Z", "bemerkung": "", "status": "ABGESAGT",
         "mandantMitarbeiterId": 208413},
        {"__typename": "PatientenTermin", "id": 2, "from": "2026-09-14T08:00:00.000Z",
         "until": "2026-09-14T08:30:00.000Z", "bemerkung": "", "status": None,
         "mandantMitarbeiterId": 208416},
        {"__typename": "SonstigerTermin", "id": 3, "from": "2026-09-14T09:00:00.000Z",
         "until": "2026-09-14T09:30:00.000Z", "bemerkung": "", "status": "IRGENDWAS_NEUES",
         "mandantMitarbeiterId": 229566},
    ]
    busy = _thevea(_termine_handler(termine)).list_busy("2026-09-14", ROOMS)

    assert [(b.room, b.start) for b in busy] == [
        ("208416", "2026-09-14T08:00:00.000Z"),   # live
        ("229566", "2026-09-14T09:00:00.000Z"),   # unknown status -> still occupies
    ]


def test_list_busy_asks_the_calendar_for_the_status_it_filters_on():
    """The filter is only as good as the query: selecting no `status` would make every appointment
    look live, and the bug would come back silently."""
    seen: dict = {}
    sent: list[str] = []

    def handler(request):
        import json as _json
        body = _json.loads(request.content)
        if "getTermine" in body.get("query", ""):
            sent.append(body["query"])
            seen.update(body["variables"])
            return httpx.Response(200, json={"data": {"termine": []}})
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    _thevea(handler).list_busy("2026-09-14", ROOMS)
    assert "status" in sent[0]


def test_list_busy_reads_the_practices_day_in_summer_and_winter():
    """The Berlin day, not the UTC one — and it shifts by an hour when the clocks change."""
    summer, winter = {}, {}
    _thevea(_termine_handler([], summer)).list_busy("2026-09-14", ROOMS)
    _thevea(_termine_handler([], winter)).list_busy("2026-11-16", ROOMS)

    assert summer["from"] == "2026-09-13T22:00:00.000Z"  # 00:00 Berlin (CEST)
    assert summer["until"] == "2026-09-14T21:59:59.000Z"
    assert winter["from"] == "2026-11-15T23:00:00.000Z"  # 00:00 Berlin (CET)
    assert winter["personenIds"] == ROOMS


def test_list_busy_surfaces_a_broken_read_as_an_error():
    conn = _thevea(lambda request: httpx.Response(500, json={}))
    with pytest.raises(TheveaError):
        conn.list_busy("2026-09-14", ROOMS)


# --- the website's copy --------------------------------------------------------------------

def test_read_day_separates_never_mirrored_from_mirrored_and_empty():
    assert _site(lambda r: httpx.Response(200, json={"synced": False, "ranges": []})).read_day(
        "2026-09-14"
    ) is None
    assert _site(lambda r: httpx.Response(200, json={"synced": True, "ranges": []})).read_day(
        "2026-09-14"
    ) == []


def test_publish_day_sends_times_and_rooms_only():
    sent: dict = {}

    def handler(request):
        sent["method"], sent["url"] = request.method, str(request.url)
        sent["body"] = json.loads(request.content)
        sent["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True, "day": "2026-09-14", "count": 1})

    _site(handler).publish_day(
        "2026-09-14",
        # Postgres spelling on the way in, canonical UTC on the way out.
        [BusyRange("208413", "2026-09-14 07:00:00+00", "2026-09-14 07:30:00+00", "HF-260911-AB12")],
    )

    assert sent["method"] == "PUT" and sent["url"].endswith("/api/admin/occupancy")
    assert sent["auth"].startswith("Basic ")
    assert sent["body"] == {
        "day": "2026-09-14",
        "ranges": [{
            "room": "208413",
            "start": "2026-09-14T07:00:00.000Z",
            "end": "2026-09-14T07:30:00.000Z",
            "site_ref": "HF-260911-AB12",
        }],
    }


def test_publish_failure_is_an_error_not_a_shrug():
    with pytest.raises(SourceError):
        _site(lambda r: httpx.Response(500, json={})).publish_day("2026-09-14", [])


# --- the binding both steps read ------------------------------------------------------------

class _Occupancy:
    def __init__(self, ranges, error=None):
        self.ranges, self.error = ranges, error

    def list_busy(self, day, room_ids):
        if self.error:
            raise self.error
        return self.ranges

    def close(self):
        pass


class _Site:
    def __init__(self, published, error=None):
        self.published, self.error = published, error
        self.writes: list = []

    def read_day(self, day):
        if self.error:
            raise self.error
        return self.published

    def publish_day(self, day, ranges):
        if self.error:
            raise self.error
        self.writes.append((day, list(ranges)))
        self.published = list(ranges)

    def close(self):
        pass


_ONE = [BusyRange("208413", "2026-09-14T07:00:00.000Z", "2026-09-14T07:30:00.000Z", None)]


def _view(occupancy, site):
    return MirrorDayProvider(occupancy, site, "2026-09-14", ROOMS).query("day_matches_occupancy")


def test_in_sync_only_when_the_website_shows_what_the_calendar_has():
    assert _view(_Occupancy(_ONE), _Site(list(_ONE))).get_field("in_sync") is True
    assert _view(_Occupancy(_ONE), _Site([])).get_field("in_sync") is False
    # Never mirrored is not "in sync with an empty day" — it still has to be published once.
    assert _view(_Occupancy([]), _Site(None)).get_field("in_sync") is False
    assert _view(_Occupancy([]), _Site([])).get_field("in_sync") is True


def test_an_unreadable_system_halts_the_day_instead_of_answering():
    with pytest.raises(StateUnavailable):
        _view(_Occupancy([], error=TheveaError("down")), _Site([]))
    with pytest.raises(StateUnavailable):
        _view(_Occupancy([]), _Site([], error=SourceError("down")))


# --- the write tool ---------------------------------------------------------------------------

def test_publish_tool_copies_the_calendar_onto_the_site_and_then_agrees():
    occupancy, site = _Occupancy(_ONE), _Site(None)
    tools = mirror_tools(occupancy, site, "2026-09-14", ROOMS)

    outcome = tools["publish_busy_day"](None, None)

    assert outcome.ok
    assert site.writes == [("2026-09-14", _ONE)]
    # What the tool claims is not what decides: the binding now re-reads and agrees.
    assert _view(occupancy, site).get_field("in_sync") is True


def test_publish_tool_reports_a_failed_write_rather_than_claiming_success():
    tools = mirror_tools(_Occupancy(_ONE), _Site(None, error=SourceError("down")), "2026-09-14", ROOMS)
    outcome = tools["publish_busy_day"](None, None)
    assert not outcome.ok and "website" in (outcome.note or "")


def test_digest_ignores_order_and_spelling():
    a = [BusyRange("208413", "2026-09-14T07:00:00.000Z", "2026-09-14T07:30:00.000Z", None),
         BusyRange("208416", "2026-09-14T08:00:00.000Z", "2026-09-14T08:30:00.000Z", "HF-260911-AB12")]
    b = list(reversed([BusyRange(r.room, r.start.replace("T", " ").replace(".000Z", "+00"),
                                 r.end.replace("T", " ").replace(".000Z", "+00"), r.site_ref)
                       for r in a]))
    assert busy_digest(a) == busy_digest(b)
