"""Studio lifecycle domain. Published snapshots are immutable; activation is operational."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.agents import numbers
from app.agents.config import AgentConfig
from app.workloads.conversational import capabilities
from app.workloads.conversational.surface import uses_realtime
from app.config import settings
from app.db import agents as store, repo
from app.db.models import Connection, Tenant
from app.workloads.conversational.calendar import VOICE_CALENDARS


class StudioError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def identifier(value):
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise StudioError("Invalid identifier", 400) from exc


async def get_agent(session, tenant_id, agent_id, *, lock=False):
    row = await store.get_agent(session, tenant_id, identifier(agent_id), lock=lock)
    if row is None:
        raise StudioError("Agent not found", 404)
    return row


def check_generation(agent, expected):
    if agent.generation != expected:
        raise StudioError("This draft changed in another session. Reload before saving again.", 409)


async def detail(session, agent):
    revisions = await store.history(session, agent)
    channel = await store.channel(session, agent)
    # What this draft can actually do, resolved by the same function the pipeline uses — so the
    # Studio shows the model's real powers rather than a hopeful reading of the config.
    powers = capabilities.resolve(AgentConfig.model_validate(agent.draft))
    # Which real system each capability acts on. A skill that reads or writes appointments is
    # useless without one, and until now the Studio showed the capability in one section and the
    # binding in another, so "booking is on" could be true while nothing could be booked.
    systems = await _systems(session, channel)
    return dict(
        id=agent.id.hex,
        config=agent.draft,
        generation=agent.generation,
        skills=list(powers.names),
        tools=list(powers.tools),
        published_instance_id=agent.published_instance_id.hex
        if agent.published_instance_id
        else None,
        history=[
            dict(
                id=r.id.hex,
                revision=r.revision,
                config=r.runtime_config,
                created_at=r.created_at.isoformat(),
            )
            for r in revisions
        ],
        channel=dict(
            phone_number=channel.phone_number,
            active=channel.active,
            instance_id=channel.instance_id.hex,
            connection_id=channel.connection_id.hex,
        )
        if channel
        else None,
        issues=AgentConfig.model_validate(agent.draft).publish_issues(),
        systems=systems,
    )


async def _systems(session, channel) -> dict:
    """The connection roles a voice agent binds, and what is in them right now.

    `supported` is the registry, not a hardcoded list: a practice-management system appears here
    the moment somebody writes a `PracticeCalendar` for it, with nothing to update in the Studio.
    """
    bound = None
    if channel is not None:
        connection = await session.get(Connection, channel.connection_id)
        if connection is not None:
            bound = dict(
                id=connection.id.hex,
                adapter=connection.adapter,
                label=(connection.config or {}).get("label") or connection.adapter,
                # A connection with no calendar mapping is connected and unusable, which is
                # exactly the state six of this tenant's connections are in.
                configured=bool(
                    (connection.config or {}).get(VOICE_CALENDARS[connection.adapter].mapping.config_key)
                )
                if connection.adapter in VOICE_CALENDARS
                else False,
                # What the agent can do on it, so the Studio can say "reads availability, staff
                # book" rather than a green tick for a capability the system cannot honour.
                capabilities=sorted(VOICE_CALENDARS[connection.adapter].capabilities)
                if connection.adapter in VOICE_CALENDARS
                else [],
            )
    return {
        "calendar": {
            "bound": bound,
            "supported": sorted(VOICE_CALENDARS),
        }
    }


async def save(session, agent, config, expected):
    check_generation(agent, expected)
    agent.draft = config.model_dump()
    agent.generation += 1
    await session.flush()


async def make_snapshot(session, agent, *, kind):
    config = AgentConfig.model_validate(agent.draft)
    if kind == "published" and config.publish_issues():
        raise StudioError(" ".join(config.publish_issues()))
    template = await repo.latest_published_template(session, "voice_appointment")
    if template is None:
        raise StudioError("The booking template is not published yet.", 503)
    # Pin all governed contracts exercised by a session, not just the display version.
    contracts = {}
    for name in ("voice_appointment", "voice_appointment_cancel", "voice_appointment_reschedule"):
        row = await repo.latest_published_template(session, name)
        if row:
            contracts[name] = row.contract
    snapshot_config = {
        **config.model_dump(),
        "contracts": contracts,
        "base_prompt": (
            Path(__file__).parents[1] / "workloads/conversational/prompts/studio.md"
        ).read_text(),
    }
    if kind == "published":
        for existing in await store.history(session, agent):
            if existing.runtime_config == snapshot_config:
                agent.published_instance_id = existing.id
                return existing
    instance = await store.snapshot(session, agent, template, snapshot_config, kind)
    if kind == "published":
        agent.published_instance_id = instance.id
    return instance


def instance_config(instance):
    raw = dict(instance.runtime_config or {})
    raw.pop("contracts", None)
    raw.pop("base_prompt", None)
    return AgentConfig.model_validate(raw)


async def owned_connection(session, tenant_id, connection_id):
    connection = await repo.get_connection(session, identifier(connection_id), tenant_id)
    if connection is None:
        raise StudioError("Connection not found in this practice.", 404)
    if connection.adapter not in VOICE_CALENDARS or connection.type != "calendar":
        names = ", ".join(system.label for system in VOICE_CALENDARS.values())
        raise StudioError(f"Select a practice calendar connection ({names}).")
    return connection


def check_calendar(connection, config):
    """Prove the agent can read the calendars it will act on, through the system's own check.

    Blocking: run it in a thread. The mapping is checked first, because a missing label is a
    sentence the practice can act on, and a failed read is not. The message says what the agent
    will actually do on this system, so a read-only system is never mistaken for one that books.
    """
    system = VOICE_CALENDARS[connection.adapter]
    mapping = (connection.config or {}).get(system.mapping.config_key) or {}
    missing = [
        name for name in config.resources if not isinstance(mapping, dict) or not mapping.get(name)
    ]
    if missing:
        raise StudioError("Map these calendars in Connections: " + ", ".join(missing))
    system.verify(connection, config)
    message = "Access and mapped calendar reads verified. No appointments were changed."
    if "booking" not in system.capabilities:
        message += (
            f" {system.label} cannot take bookings by phone yet: this agent will tell callers which"
            " times are free and pass their booking request to your team as a callback."
        )
    return {"ok": True, "message": message}


async def activate(session, agent, instance_id, connection_id, number, *, webhook_url):
    instance = await repo.get_instance(session, identifier(instance_id), agent.tenant_id)
    if instance is None or instance.agent_id != agent.id or instance.snapshot_kind != "published":
        raise StudioError("Select a published revision of this agent.")
    connection = await owned_connection(session, agent.tenant_id, connection_id)
    owner = await store.number_owner(session, number)
    if owner and owner.agent_id != agent.id:
        raise StudioError("This phone number is already assigned to another agent.", 409)
    legacy = await repo.instance_for_phone_number(session, phone_number=number)
    if legacy and legacy.agent_id != agent.id:
        raise StudioError(
            "A legacy deployment owns this number. Pause it before assigning the number here.", 409
        )
    try:
        await asyncio.to_thread(check_calendar, connection, instance_config(instance))
    except StudioError:
        raise
    except Exception as exc:
        raise StudioError(
            "Calendar access could not be verified. Reconnect and check your room mapping."
        ) from exc
    if not settings.smtp_host:
        raise StudioError("Configure email delivery before activating staff callbacks.", 503)
    live = instance_config(instance)
    # A realtime agent hears and speaks with one model, so the transcription and synthesis
    # vendors it never calls are not a reason to refuse it a phone number.
    realtime = uses_realtime(live)
    required = ("openai_api_key",) if realtime else (
        "deepgram_api_key",
        "openai_api_key",
        "elevenlabs_api_key",
    )
    for name in required:
        if not getattr(settings, name):
            raise StudioError("Voice providers are not fully configured.", 503)
    if not realtime and not (live.voice_id or settings.elevenlabs_voice_for(live.locale)):
        raise StudioError("Select a voice or configure the default voice.", 503)
    try:
        # Ownership, voice capability, and pointing the number's webhook at us: the last of these
        # used to be a manual step in the Twilio console.
        await numbers.connect(session, agent.tenant_id, number, webhook_url=webhook_url)
    except numbers.NumberError as exc:
        raise StudioError(str(exc), exc.status) from exc
    except httpx.HTTPError as exc:
        raise StudioError(
            "The phone provider could not verify this number. Try again or check the carrier configuration.",
            503,
        ) from exc
    await store.assign_channel(session, agent, instance, connection.id, number)


# Task statuses a practice still has to act on. A completed or cancelled callback is done.
_WAITING = ("pending", "live", "action_required")


async def workspace_summary(session, tenant_id) -> dict:
    """One practice at a glance, for an agency's overview (ADR-0016 D1).

    Read through the same tenant-scoped token as every other route, so the overview needs no new
    trust: an agency sees a workspace only because Clerk issued it a token for that organization.
    `attention` is what still stands between the practice and a working phone line, in words the
    agency can act on.
    """
    tenant = await session.get(Tenant, tenant_id)
    agents = await store.list_agents(session, tenant_id)
    rows = []
    for agent in agents:
        channel = await store.channel(session, agent)
        rows.append(
            {
                "id": agent.id.hex,
                "name": AgentConfig.model_validate(agent.draft).name,
                "published": agent.published_instance_id is not None,
                "live": bool(channel and channel.active),
                "phone_number": channel.phone_number if channel else None,
            }
        )
    calendars = [
        c for c in await repo.list_connections(session, tenant_id) if c.adapter in VOICE_CALENDARS
    ]
    owned_numbers = await numbers.owned(session, tenant_id)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    calls = await repo.count_conversations_since(session, tenant_id, since)
    waiting = sum(1 for t in await repo.list_tasks(session, tenant_id) if t.status in _WAITING)

    attention = []
    if not rows:
        attention.append("No agent yet.")
    if not calendars:
        attention.append("No practice calendar connected.")
    if not owned_numbers:
        attention.append("No phone number claimed yet.")
    for row in rows:
        if not row["published"]:
            attention.append(f"{row['name']} has not been published yet.")
        elif not row["live"]:
            attention.append(f"{row['name']} is not answering calls yet.")
    if waiting:
        attention.append(f"{waiting} callback{'s' if waiting != 1 else ''} waiting.")
    return {
        "name": tenant.name if tenant else "",
        "agents": rows,
        "calendars": len(calendars),
        "numbers": owned_numbers,
        "calls_7d": calls,
        "callbacks_waiting": waiting,
        "attention": attention,
    }
