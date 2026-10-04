"""Which calendar this call books into — the sandbox, or the practice's real thevea calendar.

This is the seam the whole conversational tier turns on. `BookingSession` never names a calendar
type; it is handed one that satisfies `PracticeCalendar`, so pointing the voice agent at a real
practice is a CONNECTION change rather than a code change (ADR-0004). The Studio tester and an
inbound phone call run the same agent, the same prompt, the same skills and the same governed
contracts — only what is behind the port differs.

Three rules the resolution follows, in order of how badly they fail if broken:

1. **A real binding is never quietly downgraded to the sandbox.** An instance bound to a thevea
   connection that cannot be built raises. The alternative is a caller being given a confident,
   real-sounding appointment in an in-memory dict nobody will ever read — the fabrication ADR-0003
   D4 exists to make impossible.
2. **An unmapped practice is a configuration error, not a guess.** MA1/MA2/MA3 are thevea room
   ids nobody has given us. Without them the calendar refuses to construct, rather than booking
   into whichever room id happened to be the default.
3. **No binding means the sandbox, explicitly.** A Studio session with nothing bound — or bound to
   the simulated `memory` connection the Studio creates for a rehearsal — is a rehearsal, and
   should behave like one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.resolve import client_from_connection
from app.db import repo
from app.db.models import AgentInstance
from app.providers.sandbox import SandboxCalendar
from app.providers.doctolib_calendar import DoctolibCalendarUnconfigured, DoctolibPracticeCalendar
from app.providers.thevea_calendar import TheveaCalendarUnconfigured, TheveaPracticeCalendar
from app.workloads.conversational.skills import selected_skills

log = logging.getLogger(__name__)

# The role a voice instance binds its calendar to, matching `required_connections: [calendar]` in
# the voice runbooks.
CALENDAR_ROLE = "calendar"

# The Studio's simulated stand-in (`repo.simulated_connection`). It is a real bound Connection row,
# so it is not "unbound" — but it names no system of record, and the honest calendar behind it is
# the sandbox. Rejecting it would break every instance deployed before a real practice calendar
# existed, which is all of them today.
SIMULATED_ADAPTER = "memory"

# Where the practice's own calendar names map to thevea room ids. Connection config, not code:
# they are one practice's ids, and a second practice on the same deployment has different ones.
ROOMS_KEY = "rooms"


@dataclass(frozen=True)
class Mapping:
    """How a practice's own calendar labels ("MA1") map to the system's ids, in connection config."""

    config_key: str
    label: str
    numeric: bool


@dataclass(frozen=True)
class CalendarSystem:
    """Everything system-specific about a practice system, in one place (ADR-0014 D1).

    The Studio builds its connect form from `label`, `connect` and `mapping`; activation proves
    access with `verify`; a call builds its calendar with `build`; and `capabilities` decides what
    the agent may do on it. Nothing above this entry knows which system it is.
    """

    key: str
    label: str
    # Which connect flow the Studio runs: one request, or a login that may ask for an emailed code.
    connect: Literal["password", "password_and_code"]
    mapping: Mapping
    # A subset of {"availability", "patients", "booking"}: what a call on this system can do.
    capabilities: frozenset[str]
    build: Callable[[Any, Any], Any]
    # Blocking. Proves authenticated reads of the mapped calendars; raises if they fail.
    verify: Callable[[Any, Any], None]


def _thevea(connection, practice):
    """thevea is one entry in the registry below, not a name the runtime knows."""
    rooms = _rooms_from(connection.config or {})
    connector = client_from_connection(connection, search_room_ids=list(rooms.values()))
    try:
        return TheveaPracticeCalendar(connector, rooms, practice=practice)
    except TheveaCalendarUnconfigured:
        # Close what we opened: a refused construction must not leak an authenticated session.
        connector.close()
        raise


def _verify_thevea(connection, config) -> None:
    rooms = _rooms_from(connection.config or {})
    client = client_from_connection(connection, search_room_ids=list(rooms.values()))
    try:
        TheveaPracticeCalendar(client, rooms, config.to_practice())
        now = datetime.now(ZoneInfo(config.timezone))
        client.verify()
        # Proves authenticated calendar reads, not just credential storage.
        client.termine_between(now, now + timedelta(days=1), room_ids=list(rooms.values()))
    finally:
        client.close()


def _agendas_from(config: dict[str, Any]) -> dict[str, str]:
    raw = (config or {}).get("agendas") or {}
    return {str(k): str(v).strip() for k, v in raw.items() if str(v).strip()} if isinstance(raw, dict) else {}


def _doctolib(connection, practice):
    agendas = _agendas_from(connection.config or {})
    connector = client_from_connection(connection, agenda_ids=list(agendas.values()))
    try:
        return DoctolibPracticeCalendar(connector, agendas, practice=practice)
    except DoctolibCalendarUnconfigured:
        connector.close()
        raise


def _verify_doctolib(connection, config) -> None:
    agendas = _agendas_from(connection.config or {})
    client = client_from_connection(connection, agenda_ids=list(agendas.values()))
    try:
        DoctolibPracticeCalendar(client, agendas, config.to_practice())
        now = datetime.now(ZoneInfo(config.timezone)).replace(tzinfo=None)
        client.verify()
        client.appointments_between(now, now + timedelta(days=1), list(agendas.values()))
    finally:
        client.close()


# Every system a voice agent can act on. A practice system joins by implementing the calendar port
# it can honour (`PracticeCalendar`, or only its availability half) and adding one entry here —
# never by editing the Studio, activation, the booking session, the skills or the engine
# (CLAUDE.md §XII). What it cannot do is declared in `capabilities`, and the agent loses exactly
# that (`effective_config`).
VOICE_CALENDARS: dict[str, CalendarSystem] = {
    "thevea": CalendarSystem(
        key="thevea",
        label="Thevea",
        connect="password",
        mapping=Mapping(config_key=ROOMS_KEY, label="Thevea room ID", numeric=True),
        capabilities=frozenset({"availability", "patients", "booking"}),
        build=_thevea,
        verify=_verify_thevea,
    ),
    # Availability only until its write calls are captured (ADR-0014 open question).
    "doctolib": CalendarSystem(
        key="doctolib",
        label="Doctolib",
        connect="password_and_code",
        mapping=Mapping(config_key="agendas", label="Doctolib agenda ID", numeric=False),
        capabilities=frozenset({"availability"}),
        build=_doctolib,
        verify=_verify_doctolib,
    ),
}


def effective_config(config, system: CalendarSystem | None):
    """The agent a call actually runs: what the practice allowed, minus what its system cannot do.

    A capability is taken away here, never granted (ADR-0014 D2). On a system that can read but not
    book, an agent that was meant to book instead reads real times and hands the booking to staff
    (`check_availability`). The published snapshot is untouched, so the same agent books on its
    next call once the system can.
    """
    if config is None or system is None or "booking" in system.capabilities:
        return config
    chosen = (
        [skill.name for skill in selected_skills()] if config.skills is None else list(config.skills)
    )
    meant_to_book = config.booking_enabled and "book_appointment" in chosen
    kept = [name for name in chosen if name not in ("book_appointment", "change_appointment")]
    if meant_to_book and "availability" in system.capabilities:
        kept.append("check_availability")
    return config.model_copy(update={"booking_enabled": False, "skills": kept})


def _rooms_from(config: dict[str, Any]) -> dict[str, int]:
    """`{"MA1": 4711, ...}` out of the connection config, tolerating string ids from a form."""
    raw = (config or {}).get(ROOMS_KEY) or {}
    rooms: dict[str, int] = {}
    for name, value in raw.items():
        try:
            rooms[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return rooms


async def resolve_calendar(
    session: AsyncSession, instance: AgentInstance | None, *, practice=None
) -> tuple[Any, str]:
    """The calendar this call books into, and a one-word label for the trace and the summary.

    Returns `(calendar, "sandbox" | <registry key>)`. Raises rather than falling back when a real
    binding exists but cannot be honoured — see rule 1 in the module docstring.
    """
    if instance is None:
        return SandboxCalendar(), "sandbox"

    connection = await repo.instance_connection(
        session, instance_id=instance.id, role=CALENDAR_ROLE
    )
    if connection is None:
        return SandboxCalendar(), "sandbox"

    if connection.adapter == SIMULATED_ADAPTER:
        return SandboxCalendar(), "sandbox"

    system = VOICE_CALENDARS.get(connection.adapter)
    if system is None:
        raise RuntimeError(
            f"no voice calendar for {connection.adapter!r} — the system is connected, but nothing "
            "implements PracticeCalendar for it yet, so a caller could not be told what is free. "
            f"Bind one of {sorted(VOICE_CALENDARS)}, or the simulated connection to rehearse."
        )

    if connection.tenant_id != instance.tenant_id:
        raise RuntimeError("Calendar connection does not belong to this practice")

    return system.build(connection, practice), connection.adapter
