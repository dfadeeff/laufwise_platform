"""Resolve a single immutable voice snapshot before opening browser or phone media."""

from datetime import datetime, timedelta, timezone

from app.agents.config import AgentConfig
from app.agents.service import instance_config, instance_knowledge, StudioError
from app.providers.sandbox import SandboxCalendar
from app.db import repo
from app.db.session import get_sessionmaker
from app.db import agents as agent_store
from app.workloads.conversational.calendar import VOICE_CALENDARS, effective_config, resolve_calendar
from app.workloads.conversational.sessions import (
    TOKEN_TTL_SECONDS,
    VoiceSession,
    new_token,
    token_digest,
)


async def prepare_voice(session, instance, *, rehearsal, calendar_mode="sandbox", connection=None):
    """The calendar and the effective agent for one call.

    A rehearsal uses the sandbox unless the Studio asked to test on a real calendar (ADR-0018):
    then the chosen connection's system builds it, exactly as on a live call, and the agent is
    limited to what that system can do. Whether the test may write is the booking session's
    concern (`test_mode`), not the calendar's.
    """
    if not getattr(instance, "runtime_config", None):
        if not rehearsal:
            raise StudioError("Move this legacy voice deployment into Studio before activating it.")
        config = AgentConfig()
    else:
        config = instance_config(instance)
    practice = config.to_practice()
    if rehearsal and calendar_mode != "sandbox" and connection is not None:
        system = VOICE_CALENDARS[connection.adapter]
        calendar, kind = system.build(connection, practice), connection.adapter
        config = effective_config(config, system)
    elif rehearsal:
        calendar, kind = SandboxCalendar(practice), "sandbox"
        # A rehearsal books into the sandbox, but it rehearses the agent the practice's phone will
        # actually run: on a system that cannot book, the rehearsal cannot book either.
        if session is not None and getattr(instance, "agent_id", None):
            adapter = await agent_store.bound_adapter(session, instance.tenant_id, instance.agent_id)
            config = effective_config(config, VOICE_CALENDARS.get(adapter))
    else:
        if instance.snapshot_kind != "published":
            raise StudioError("Only published revisions can answer phone calls.")
        calendar, kind = await resolve_calendar(session, instance, practice=practice)
        # Which real systems a caller can be booked into is the voice registry's decision
        # (`VOICE_CALENDARS`), not this function's. The one thing a phone must never reach is the
        # sandbox: a caller told "you're booked" into memory nobody reads is a fabrication.
        if kind == "sandbox":
            raise StudioError("A live phone agent needs a real practice calendar.")
        config = effective_config(config, VOICE_CALENDARS.get(kind))
    return calendar, kind, config


async def admit_voice_call(
    session, conversation_id, *, language, rehearsal, caller_number=None, recall=None,
    caller_hash=None, agent_id=None, calendar_mode="sandbox", connection_id=None,
) -> str:
    """Mint the token a media socket trades for this call. Only its hash is stored."""
    token = new_token()
    await repo.admit_voice_call(
        session,
        token_hash=token_digest(token),
        conversation_id=conversation_id,
        language=language,
        rehearsal=rehearsal,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS),
        caller_number=caller_number,
        recall=recall,
        caller_hash=caller_hash,
        agent_id=agent_id,
        calendar_mode=calendar_mode,
        connection_id=connection_id,
    )
    return token


async def open_voice_call(token, *, sessionmaker=None) -> VoiceSession:
    """Trade a token for its call, rebuilt from the database by whichever process got the socket.

    Raises KeyError for a token that is unknown, used or expired. The calendar is resolved again
    rather than carried over: the conversation pins the immutable instance the webhook resolved,
    so both resolutions read the same snapshot.
    """
    async with (sessionmaker or get_sessionmaker())() as session:
        redeemed = await repo.redeem_voice_call(session, token_digest(token))
        if redeemed is None:
            raise KeyError(token)
        admitted, conversation = redeemed
        instance = await repo.get_instance(session, conversation.instance_id, conversation.tenant_id)
        if instance is None:
            raise KeyError(token)
        mode = admitted.get("calendar_mode") or "sandbox"
        connection = None
        if mode != "sandbox":
            connection = await repo.get_connection(
                session, admitted["connection_id"], conversation.tenant_id
            )
            if connection is None:
                raise StudioError("The calendar account for this test no longer exists.")
        calendar, _kind, config = await prepare_voice(
            session, instance, rehearsal=admitted["rehearsal"], calendar_mode=mode,
            connection=connection,
        )
    runtime_config = instance.runtime_config or {}
    return VoiceSession(
        tenant_id=str(conversation.tenant_id),
        language=admitted["language"],
        conversation_id=conversation.id,
        caller_number=admitted["caller_number"],
        calendar=calendar,
        config=config,
        contracts=runtime_config.get("contracts"),
        rehearsal=admitted["rehearsal"],
        base_prompt=runtime_config.get("base_prompt"),
        recall=admitted["recall"],
        caller_hash=admitted["caller_hash"],
        agent_id=admitted["agent_id"],
        knowledge=instance_knowledge(instance),
        calendar_mode=mode,
    )
