"""Studio lifecycle domain. Published snapshots are immutable; activation is operational."""

import asyncio
import re
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.agents.config import AgentConfig
from app.config import settings
from app.connections.resolve import client_from_connection
from app.db import agents as store, repo
from app.providers.thevea_calendar import TheveaPracticeCalendar


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
    return dict(
        id=agent.id.hex,
        config=agent.draft,
        generation=agent.generation,
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
    )


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
    if connection.adapter != "thevea" or connection.type != "calendar":
        raise StudioError("Select a Thevea calendar connection.")
    return connection


def check_calendar(connection, config):
    rooms = (connection.config or {}).get("rooms", {})
    missing = [
        name for name in config.resources if not isinstance(rooms, dict) or not rooms.get(name)
    ]
    if missing:
        raise StudioError("Map these calendars in Connections: " + ", ".join(missing))
    client = client_from_connection(connection, search_room_ids=list(rooms.values()))
    try:
        TheveaPracticeCalendar(client, rooms, config.to_practice())
        now = datetime.now(ZoneInfo(config.timezone))
        client.verify()
        # Proves authenticated calendar reads, not just credential storage.
        client.termine_between(now, now + timedelta(days=1), room_ids=list(rooms.values()))
        return {
            "ok": True,
            "message": "Access and mapped calendar reads verified. No appointments were changed.",
        }
    finally:
        client.close()


async def verify_number(number, tenant_id):
    if settings.voice_number_assignments.get(number) != str(tenant_id):
        raise StudioError(
            "Ask your administrator to assign this phone number to your practice in VOICE_NUMBER_ASSIGNMENTS."
        )
    if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
        raise StudioError("Enter an international number such as +493012345678.")
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise StudioError(
            "Phone service is not configured. Ask your administrator to configure Twilio.", 503
        )
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": number},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
        response.raise_for_status()
        if not any(
            row.get("phone_number") == number and row.get("capabilities", {}).get("voice")
            for row in response.json().get("incoming_phone_numbers", [])
        ):
            raise StudioError(
                "This number is not a voice-enabled number in the configured Twilio account."
            )


async def activate(session, agent, instance_id, connection_id, number):
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
    for name in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key"):
        if not getattr(settings, name):
            raise StudioError("Voice providers are not fully configured.", 503)
    if not (
        instance_config(instance).voice_id
        or settings.elevenlabs_voice_for(instance_config(instance).locale)
    ):
        raise StudioError("Select a voice or configure the default voice.", 503)
    try:
        await verify_number(number, agent.tenant_id)
    except httpx.HTTPError as exc:
        raise StudioError(
            "The phone provider could not verify this number. Try again or check the carrier configuration.",
            503,
        ) from exc
    await store.assign_channel(session, agent, instance, connection.id, number)
