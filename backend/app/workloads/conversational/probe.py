"""Does this agent's Thevea binding actually work — reads, and if you ask for it, a real write.

There is a gap between the two things you can test today. A Studio rehearsal always runs against
the in-memory sandbox (`app/agents/runtime.py`), so it proves the conversation and nothing about
the calendar. A live phone call proves everything at once, on a real caller, which is a poor place
to discover that a room id is wrong or that the practice hours in the config do not match the ones
in Thevea.

This is the missing middle. It drives the SAME objects a call drives — the agent's own published
configuration, the connection its channel is bound to, `TheveaPracticeCalendar`, `BookingSession`,
and the governed contract with its postcondition — with the dialogue replaced by fixed arguments.
If this passes, a phone call has nothing left to fail on except the phone.

    python -m app.workloads.conversational.probe --agent <agent id>
    python -m app.workloads.conversational.probe --connection <id> --rooms MA1=208413,MA2=208416
    python -m app.workloads.conversational.probe --connection <id> --rooms ... --book

`--connection` skips the Studio entirely: it needs only a stored Thevea connection and the room
ids, so the calendar can be proven before an agent exists, before a number is assigned, and
before anything is published. That is the order you want when something is wrong — the calendar
is the part with the most ways to be misconfigured, and the phone is the part with the fewest.

Without `--book` it only reads: the free slots the agent would offer over the next fortnight. That
alone catches most of what goes wrong — wrong room ids, an empty grid, hours that disagree with
the practice, credentials that no longer log in.

`--book` writes a REAL appointment into the practice's REAL calendar, because that is the only
thing that proves a write. There is no undo: the platform has no delete capability by design
(ADR-0004 D7), so whatever this books has to be removed in Thevea by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import date, timedelta

from app.agents import service
from app.db import agents as store
from app.db.session import get_sessionmaker
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.calendar import resolve_calendar

# A name no real patient shares, so a probe booking is obvious in the practice's calendar and
# nobody spends a morning wondering who it is.
PROBE_PATIENT = {
    "first_name": "Laufwise",
    "last_name": "Testbuchung",
    "date_of_birth": "1990-01-01",
    "phone": "+4900000000000",
}


async def probe(
    agent_id: str | None, *, days: int, book: bool, connection_id: str | None, rooms: str | None
) -> int:
    async with get_sessionmaker()() as session:
        if connection_id:
            config, calendar, kind = await _direct(session, connection_id, rooms)
        else:
            agent = await _agent(session, agent_id or "")
            config = await _config(session, agent)
            instance = await _bound_instance(session, agent)
            calendar, kind = await resolve_calendar(
                session, instance, practice=config.to_practice()
            )
        practice = config.to_practice()

    print(f"agent:      {config.name} · {config.practice_name or 'practice not named'}")
    print(f"calendar:   {kind}")
    if kind != "thevea":
        print("\nThis agent is not bound to a real Thevea calendar, so there is nothing to probe.")
        print("Connect Thevea in Governance, then bind it in the agent's Phone & handoff section.")
        return 1
    print(f"rooms:      {', '.join(practice.schedule.resources)}")
    print(f"hours:      {practice.schedule.periods}")

    try:
        slots = calendar.free_slots(
            date_from=date.today(), date_to=date.today() + timedelta(days=days), limit=5
        )
        print(f"\nfree slots in the next {days} days (what the agent would offer):")
        for slot in slots:
            print(f"  {slot.start}  {getattr(slot, 'resource', '')}")
        if not slots:
            print("  none — check the practice hours, the room mapping, and the date window")
            return 1

        if not book:
            print("\nReads work. Add --book to prove the write, which creates a real appointment.")
            return 0

        return _book(calendar, practice, slots[0])
    finally:
        if hasattr(calendar, "close"):
            calendar.close()


def _book(calendar, practice, slot) -> int:
    """Book through the agent's own path, so the engine decides whether it worked."""
    print(f"\nbooking {slot.start} — a REAL appointment, which cannot be deleted from here")
    call = BookingSession(f"probe-{uuid.uuid4().hex[:8]}", calendar=calendar, practice=practice)
    call.set_details(
        **PROBE_PATIENT,
        preferred_time=slot.start,
        service_key=practice.services[0].key,
    )
    # find_patient is what the booking contract's `patient_checked` precondition reads; skipping it
    # here would prove a path no caller can take.
    print(f"  patient lookup: {call.find_patient()['result']}")
    confirmed = call.confirm(
        read_back=f"{PROBE_PATIENT['first_name']} {PROBE_PATIENT['last_name']}, {slot.start}"
    )
    if not confirmed.get("confirmed"):
        print(f"  refused at confirmation: {confirmed.get('reason')}")
        return 1

    result = call.book()
    print(f"  engine ruling: {result['status']}")
    if reason := result.get("reason"):
        print(f"  reason: {reason}")
    if result["status"] != "ok":
        print("\nThe write did NOT land. The engine's reason above is the whole explanation.")
        return 1

    # The postcondition already re-read the calendar; this reads it once more, from the outside,
    # because "the tool said ok" is exactly the claim this platform exists to distrust.
    written = calendar.find_appointment(call.booked_ref)
    print(f"\nbooked:     {call.booked_ref}")
    print(f"read back:  {written.start if written else 'NOT FOUND — investigate before calling'}")
    print("\nRemove this appointment in Thevea when you are done.")
    return 0 if written else 1


async def _direct(session, connection_id: str, rooms: str | None):
    """A calendar built from a stored connection and room ids, with a default practice grid.

    The same class a call uses, reached without the Studio. The practice is `AgentConfig()`'s
    default — weekdays, 09:00-18:00 with a midday break, 30-minute slots — so the slots printed
    are a plain reading of the grid rather than a claim about this practice's real hours.
    """
    from app.agents.config import AgentConfig
    from app.connections.resolve import client_from_connection
    from app.db.models import Connection
    from app.providers.thevea_calendar import TheveaPracticeCalendar

    connection = await session.get(Connection, uuid.UUID(connection_id))
    if connection is None:
        raise SystemExit(f"no connection {connection_id}")
    if connection.adapter != "thevea":
        raise SystemExit(f"connection {connection_id} is a {connection.adapter} connection")

    mapping = _rooms(rooms) or {
        str(name): int(value) for name, value in (connection.config or {}).get("rooms", {}).items()
    }
    if not mapping:
        raise SystemExit(
            "No rooms. Pass --rooms MA1=208413,MA2=208416,... or store them on the connection."
        )
    config = AgentConfig(name="probe", resources=list(mapping))
    connector = client_from_connection(connection, search_room_ids=list(mapping.values()))
    return config, TheveaPracticeCalendar(connector, mapping, practice=config.to_practice()), "thevea"


def _rooms(raw: str | None) -> dict[str, int]:
    """`MA1=208413,MA2=208416` — the labels are yours; the ids are Thevea's."""
    mapping: dict[str, int] = {}
    for pair in (raw or "").split(","):
        if "=" in pair:
            label, _, value = pair.partition("=")
            mapping[label.strip()] = int(value.strip())
    return mapping


async def _agent(session, agent_id: str):
    from sqlalchemy import select

    from app.db.models import StudioAgent

    try:
        parsed = uuid.UUID(agent_id)
    except ValueError:
        raise SystemExit(f"{agent_id!r} is not an agent id") from None
    found = (
        await session.execute(select(StudioAgent).where(StudioAgent.id == parsed))
    ).scalar_one_or_none()
    if found is None:
        raise SystemExit(f"no agent {agent_id}")
    return found


async def _config(session, agent):
    """The published revision if there is one — that is what a caller would reach."""
    published = [r for r in await store.history(session, agent)]
    if published:
        return service.instance_config(published[0])
    print("note: this agent has no published revision; probing the draft instead")
    from app.agents.config import AgentConfig

    return AgentConfig.model_validate(agent.draft)


async def _bound_instance(session, agent):
    channel = await store.channel(session, agent)
    if channel is None:
        raise SystemExit(
            "This agent has no calendar bound yet. Connect Thevea in its Phone & handoff section."
        )
    from app.db.models import AgentInstance

    return await session.get(AgentInstance, channel.instance_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--agent", help="the Studio agent id")
    parser.add_argument("--connection", help="a stored thevea connection id, instead of an agent")
    parser.add_argument("--rooms", help="MA1=208413,MA2=208416,... when the connection has none")
    parser.add_argument("--days", type=int, default=14, help="how far ahead to look for slots")
    parser.add_argument(
        "--book",
        action="store_true",
        help="create a real appointment in the practice's real calendar (no undo)",
    )
    args = parser.parse_args()
    if not args.agent and not args.connection:
        parser.error("pass --agent or --connection")
    raise SystemExit(
        asyncio.run(
            probe(
                args.agent,
                days=args.days,
                book=args.book,
                connection_id=args.connection,
                rooms=args.rooms,
            )
        )
    )


if __name__ == "__main__":
    main()
