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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.resolve import client_from_connection
from app.db import repo
from app.db.models import AgentInstance
from app.providers.sandbox import SandboxCalendar
from app.providers.thevea_calendar import TheveaCalendarUnconfigured, TheveaPracticeCalendar

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
    session: AsyncSession, instance: AgentInstance | None
) -> tuple[Any, str]:
    """The calendar this call books into, and a one-word label for the trace and the summary.

    Returns `(calendar, "sandbox" | "thevea")`. Raises rather than falling back when a real
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

    if connection.adapter != "thevea":
        raise RuntimeError(
            f"the voice agent has no calendar for adapter {connection.adapter!r} — "
            "bind a thevea connection, or the simulated one to rehearse in the sandbox"
        )

    rooms = _rooms_from(connection.config or {})
    connector = client_from_connection(connection)
    try:
        return TheveaPracticeCalendar(connector, rooms), "thevea"
    except TheveaCalendarUnconfigured:
        # Close what we opened: a refused construction must not leak an authenticated session.
        connector.close()
        raise
