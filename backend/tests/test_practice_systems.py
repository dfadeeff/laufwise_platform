"""A practice connects the system its calendar lives in; nothing above the registry knows which.

ADR-0014. Thevea and Doctolib are both entries in one registry. Each declares how it connects, how
its calendars are mapped and what it can do on a call, and the agent a caller reaches is what the
practice allowed intersected with what the system supports. No network: Doctolib's agenda API is
answered by a scripted connector or `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.calendar import VOICE_CALENDARS, effective_config
from app.workloads.conversational.capabilities import resolve


def _monday(weeks_ahead: int = 2) -> date:
    day = date.today() + timedelta(weeks=weeks_ahead)
    return day - timedelta(days=day.weekday())


# --- the registry ----------------------------------------------------------------------------


def test_every_system_declares_what_the_studio_and_activation_need() -> None:
    """The Studio builds its connect form and activation proves access from these fields alone."""
    assert {"thevea", "doctolib"} <= set(VOICE_CALENDARS)
    for key, system in VOICE_CALENDARS.items():
        assert system.key == key and system.label
        assert system.connect in ("password", "password_and_code")
        assert system.mapping.config_key and system.mapping.label
        assert system.capabilities <= {"availability", "patients", "booking", "changes"}
        assert callable(system.build) and callable(system.verify)


def test_thevea_books_and_doctolib_reads_until_its_writes_are_captured() -> None:
    assert VOICE_CALENDARS["thevea"].capabilities == {"availability", "patients", "booking", "changes"}
    assert VOICE_CALENDARS["doctolib"].capabilities == {"availability"}
    assert VOICE_CALENDARS["doctolib"].connect == "password_and_code"


# --- the agent a caller reaches is the agent the system can support --------------------------


def test_an_agent_on_a_read_only_system_tells_callers_what_is_free_and_hands_bookings_to_staff() -> None:
    """Turning booking off used to take availability with it. A system that can read but not
    write now gets an agent that reads real times and takes the booking as a callback."""
    live = effective_config(AgentConfig(), VOICE_CALENDARS["doctolib"])
    powers = resolve(live)

    assert live.booking_enabled is False
    assert "check_availability" in powers.names
    assert {"book_appointment", "change_appointment"}.isdisjoint(powers.names)
    assert {"search_availability", "create_callback_request"} <= set(powers.tools)
    assert {"appointment_book", "appointment_set_details", "find_patient",
            "get_patient_appointments"}.isdisjoint(powers.tools)


def test_a_system_that_can_book_leaves_the_agent_exactly_as_published() -> None:
    published = AgentConfig(skills=["book_appointment", "practice_info"])

    assert effective_config(published, VOICE_CALENDARS["thevea"]) == published


def test_a_system_never_grants_what_the_practice_switched_off() -> None:
    """Capabilities are intersected, never added: no availability skill for an agent whose
    practice chose answers only."""
    info_only = AgentConfig(skills=["practice_info"], booking_enabled=False)

    live = effective_config(info_only, VOICE_CALENDARS["doctolib"])

    assert resolve(live).names == ("practice_info",)


def test_the_availability_skill_is_opt_in_so_no_other_agent_changes() -> None:
    """The knowledge-base agent, the eval replay and every agent whose system books keep the
    prompt they had: `prompt_sha` must not move for them."""
    assert "check_availability" not in resolve(None).names
    assert "check_availability" not in resolve(AgentConfig()).names


def test_a_live_doctolib_call_runs_the_agent_its_system_can_support(monkeypatch) -> None:
    from app.agents import runtime

    async def resolved(*_args, **_kwargs):
        return object(), "doctolib"

    monkeypatch.setattr(runtime, "resolve_calendar", resolved)
    instance = SimpleNamespace(runtime_config=AgentConfig().model_dump(), snapshot_kind="published")

    _calendar, kind, config = asyncio.run(runtime.prepare_voice(None, instance, rehearsal=False))

    assert kind == "doctolib" and config.booking_enabled is False


# --- Doctolib availability: the practice's grid minus what Doctolib has booked ----------------


class _Agendas:
    """Doctolib's agenda read, scripted: agenda id -> appointment rows."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.closed = False

    def appointments_between(self, start, end, agenda_ids):
        return [(agenda, row) for agenda in agenda_ids for row in self.rows.get(agenda, [])]

    def close(self) -> None:
        self.closed = True


def _row(start: datetime, minutes: int = 30, status: str = "confirmed") -> dict[str, Any]:
    end = start + timedelta(minutes=minutes)
    return {"id": f"a-{start:%H%M}", "status": status,
            "start_date": f"{start:%Y-%m-%dT%H:%M:%S}+02:00",
            "end_date": f"{end:%Y-%m-%dT%H:%M:%S}+02:00"}


def _calendar(rows: dict[str, list[dict[str, Any]]], resources=("MA1", "MA2")):
    from app.providers.doctolib_calendar import DoctolibPracticeCalendar

    practice = AgentConfig(
        open_from="09:00", open_until="11:00", break_from="", break_until="",
        resources=list(resources), timezone="Europe/Berlin",
    ).to_practice()
    return DoctolibPracticeCalendar(_Agendas(rows), {"MA1": "111", "MA2": "222"}, practice=practice)


def test_a_slot_booked_in_every_agenda_is_never_offered() -> None:
    day = _monday()
    nine = datetime.combine(day, time(9))
    calendar = _calendar({"111": [_row(nine)], "222": [_row(nine)]})

    starts = [s.start for s in calendar.free_slots(date_from=day, date_to=day, limit=10, now=datetime(2000, 1, 1))]

    assert f"{day.isoformat()}T09:00" not in starts
    assert f"{day.isoformat()}T09:30" in starts


def test_a_slot_free_in_one_agenda_is_offered_on_that_agenda() -> None:
    day = _monday()
    nine = datetime.combine(day, time(9))
    calendar = _calendar({"111": [_row(nine)]})

    assert calendar.any_resource_free(f"{day.isoformat()}T09:00") == "MA2"


def test_a_cancelled_appointment_frees_its_slot_and_an_unknown_status_does_not() -> None:
    """Fail-closed: a status we have not seen can only make the agent offer less."""
    day = _monday()
    nine = datetime.combine(day, time(9))
    both = ("MA1",)

    assert _calendar({"111": [_row(nine, status="canceled")]}, both).any_resource_free(
        f"{day.isoformat()}T09:00") == "MA1"
    assert _calendar({"111": [_row(nine, status="mystery")]}, both).any_resource_free(
        f"{day.isoformat()}T09:00") is None


def test_an_unreadable_appointment_stops_the_read_instead_of_freeing_a_slot() -> None:
    day = _monday()
    calendar = _calendar({"111": [{"id": "x", "status": "confirmed", "start_date": "soon"}]})

    with pytest.raises(Exception, match="cannot verify availability"):
        calendar.any_resource_free(f"{day.isoformat()}T09:00")


def test_a_calendar_the_practice_never_mapped_refuses_to_construct() -> None:
    from app.providers.doctolib_calendar import DoctolibCalendarUnconfigured, DoctolibPracticeCalendar

    practice = AgentConfig(resources=["MA1", "MA2", "MA3"]).to_practice()
    with pytest.raises(DoctolibCalendarUnconfigured, match="MA3"):
        DoctolibPracticeCalendar(_Agendas({}), {"MA1": "111", "MA2": "222"}, practice=practice)


def test_the_connector_reads_each_mapped_agenda_for_the_window() -> None:
    from app.providers.doctolib import DoctolibConnector

    asked: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.url.params))
        return httpx.Response(200, json={"data": [{"id": "a1", "status": "confirmed"}]})

    connector = DoctolibConnector(
        "https://pro.doctolib.de", "u", "p", session_cookie="s",
        transport=httpx.MockTransport(handler),
    )
    start, end = datetime(2026, 10, 5, 0, 0), datetime(2026, 10, 5, 23, 59)

    found = connector.appointments_between(start, end, ["111", "222"])

    assert [agenda for agenda, _row in found] == ["111", "222"]
    assert [q["agenda_ids"] for q in asked] == ["111", "222"]
    assert asked[0]["start_date"] == "2026-10-05 00:00:00"


# --- activation proves access through the system's own check ---------------------------------


def test_activation_accepts_any_registered_system_and_refuses_the_rest(monkeypatch) -> None:
    from app.agents import service

    async def get_connection(_session, _cid, _tenant):
        return connection

    monkeypatch.setattr(service.repo, "get_connection", get_connection)
    connection = SimpleNamespace(adapter="doctolib", type="calendar")
    tenant = "t"
    assert asyncio.run(service.owned_connection(None, tenant, "0" * 32)) is connection

    connection = SimpleNamespace(adapter="healthyfeet", type="calendar")
    with pytest.raises(service.StudioError):
        asyncio.run(service.owned_connection(None, tenant, "0" * 32))


def test_the_check_runs_the_system_s_own_verify_and_says_what_the_agent_will_do(monkeypatch) -> None:
    from app.agents import service

    checked: list[Any] = []
    system = VOICE_CALENDARS["doctolib"]
    monkeypatch.setitem(
        VOICE_CALENDARS, "doctolib", system.__class__(**{**system.__dict__, "verify": lambda c, cfg: checked.append(c)})
    )
    connection = SimpleNamespace(adapter="doctolib", config={"agendas": {"MA1": "1", "MA2": "2", "MA3": "3"}})

    result = service.check_calendar(connection, AgentConfig())

    assert checked == [connection]
    assert result["ok"] is True
    assert "callback" in result["message"]


def test_the_check_names_calendars_the_practice_has_not_mapped(monkeypatch) -> None:
    from app.agents import service

    connection = SimpleNamespace(adapter="doctolib", config={"agendas": {"MA1": "1"}})

    with pytest.raises(service.StudioError, match="MA2, MA3"):
        service.check_calendar(connection, AgentConfig())


# --- the Studio reads the registry ----------------------------------------------------------


def test_the_studio_is_told_every_system_and_how_to_connect_it() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import current_tenant
    from app.api.v1 import connections

    app = FastAPI()
    app.include_router(connections.router, prefix="/connections")
    app.dependency_overrides[current_tenant] = lambda: SimpleNamespace(id="t")

    systems = {s["key"]: s for s in TestClient(app).get("/connections/systems").json()}

    assert systems["doctolib"]["connect"] == "password_and_code"
    assert systems["doctolib"]["mapping"]["config_key"] == "agendas"
    assert systems["doctolib"]["capabilities"] == ["availability"]
    assert systems["thevea"]["mapping"]["numeric"] is True


def test_connecting_doctolib_keeps_the_mapping_and_reads_the_mapped_agendas(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import current_tenant
    from app.api.v1 import connections

    started: dict[str, Any] = {}

    def start_login(tenant_id, username, password, agenda_ids, config):
        started.update(agenda_ids=agenda_ids, config=config)
        return SimpleNamespace(id="job", status="starting", error=None, connection_id=None)

    monkeypatch.setattr(connections.crypto, "encrypt", lambda value: value)
    monkeypatch.setattr(connections.doctolib_login_jobs, "start_login", start_login)
    app = FastAPI()
    app.include_router(connections.router, prefix="/connections")
    app.dependency_overrides[current_tenant] = lambda: SimpleNamespace(id=SimpleNamespace(hex="t"))

    response = TestClient(app).post(
        "/connections/doctolib/login",
        json={"username": "u", "password": "p", "label": "Praxis",
              "agendas": {"MA1": "2570190", "MA2": "2557171", "MA3": "2570190"}},
    )

    assert response.status_code == 200
    assert started["agenda_ids"] == "2570190,2557171"
    assert started["config"] == {
        "label": "Praxis", "agendas": {"MA1": "2570190", "MA2": "2557171", "MA3": "2570190"}
    }


def test_a_rehearsal_runs_the_agent_the_practice_s_phone_will_run(monkeypatch) -> None:
    """A practice on Doctolib must not rehearse a booking its real calls cannot make."""
    from app.agents import runtime

    async def bound(_session, _tenant, _agent):
        return "doctolib"

    monkeypatch.setattr(runtime.agent_store, "bound_adapter", bound)
    instance = SimpleNamespace(
        runtime_config=AgentConfig().model_dump(), agent_id="a", tenant_id="t"
    )

    _calendar, kind, config = asyncio.run(runtime.prepare_voice(object(), instance, rehearsal=True))

    assert kind == "sandbox" and config.booking_enabled is False


def test_an_agent_that_cannot_book_is_told_so_in_its_instructions() -> None:
    from app.workloads.conversational.surface import _instructions

    live = effective_config(AgentConfig(), VOICE_CALENDARS["doctolib"])

    assert "cannot book" in _instructions("de", live)
    assert "cannot book" not in _instructions("de", AgentConfig())


def test_the_thevea_activation_check_runs_against_the_real_connector(monkeypatch) -> None:
    """A stub client hid that the check called a read the connector no longer has
    (`termine_between`, renamed in ADR-0011): every Thevea activation was refused."""
    import json as _json

    from app.providers.thevea import TheveaConnector
    from app.workloads.conversational import calendar as voice_calendar

    asked: list[str] = []

    def handler(request):
        body = _json.loads(request.content)
        query = body.get("query", "")
        if "getTermineUndAbwesenheiten" in query:
            asked.append(str(body["variables"]["personenIds"]))
            return httpx.Response(200, json={"data": {"termine": [], "mitarbeiterAbwesenheitenFuerZeitraum": []}})
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    rooms = {"MA1": 208413, "MA2": 208416, "MA3": 229566, "MA4": 240570}
    monkeypatch.setattr(
        voice_calendar,
        "client_from_connection",
        lambda conn, **opts: TheveaConnector(
            "https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler), **opts
        ),
    )
    connection = SimpleNamespace(adapter="thevea", config={"rooms": rooms})

    voice_calendar.VOICE_CALENDARS["thevea"].verify(
        connection, AgentConfig(resources=list(rooms))
    )

    assert asked == [str(list(rooms.values()))]
