"""Transcripts are deleted automatically after the retention period; audio never exists.

Spec §4.1 tells callers their conversation is kept for a fixed period and then deleted, and §7
makes that a technical requirement. This tests the two halves that can actually rot: that the
sweep deletes what it should and keeps what it must, and that the period comes from the practice
knowledge base rather than a constant someone can change without the practice noticing.

The database half talks to the real Supabase project and skips cleanly if it is unreachable
(ADR-0001), deleting every row it creates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import repo
from app.db.models import Conversation, ConversationEvent
from app.workloads.conversational.practice import load_practice


def test_the_retention_period_comes_from_the_practice_knowledge_base() -> None:
    """A promise made to patients belongs beside the rest of them, not in an env var."""
    policy = load_practice().policy

    assert policy.transcript_retention_days == 30
    assert policy.store_audio is False


def test_no_code_path_writes_audio() -> None:
    """Spec §4.1: "Call audio is not stored."

    Asserted as an absence rather than a policy — the pipeline has no recorder, no file sink and
    no audio buffer, and this is what notices if one is ever added.
    """
    from app.workloads.conversational import recording, surface

    for module in (recording, surface):
        source = open(module.__file__, encoding="utf-8").read()
        for forbidden in ("AudioBufferProcessor", "save_audio", "wav", "audio_out_file"):
            assert forbidden not in source, f"{module.__name__} may now be storing audio"


# --- the sweep itself, against the real database ---


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _db_reachable() -> bool:
    try:
        _run_db(lambda s: s.execute(select(1)))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="Supabase DB not reachable")
def test_an_expired_transcript_is_deleted_but_the_call_itself_is_not() -> None:
    """The line between honouring a retention period and destroying the audit trail.

    Afterwards the practice can still see that a call happened, when and how it ended — a
    `call_id` quoted in a five-week-old summary email still resolves to something. It just cannot
    be read back.
    """
    tenant = _run_db(lambda s: repo.default_tenant(s))
    instance = _run_db(
        lambda s: repo.studio_voice_instance(
            s, tenant_id=tenant.id, template_name="voice_appointment"
        )
    )
    if instance is None:
        pytest.skip("voice_appointment is not published in this database")

    async def seed(session: AsyncSession):
        old = await repo.create_conversation(
            session,
            tenant_id=tenant.id,
            instance_id=instance.id,
            channel="voice",
            direction="inbound",
            metadata={"surface": "retention-test"},
        )
        fresh = await repo.create_conversation(
            session,
            tenant_id=tenant.id,
            instance_id=instance.id,
            channel="voice",
            direction="inbound",
            metadata={"surface": "retention-test"},
        )
        for conversation in (old, fresh):
            await repo.append_conversation_event(
                session,
                conversation_id=conversation.id,
                kind="turn",
                payload={"role": "caller", "text": "Guten Tag"},
            )
        # Backdate one past the retention period. Written directly because `started_at` is a
        # server default — the point is a conversation that IS old, not one that claims to be.
        old.started_at = datetime.now(timezone.utc) - timedelta(days=45)
        await session.commit()
        return old.id, fresh.id

    old_id, fresh_id = _run_db(seed)

    try:
        cleared = _run_db(lambda s: repo.purge_expired_transcripts(s, older_than_days=30))

        async def check(session: AsyncSession):
            events = await session.execute(
                select(ConversationEvent.conversation_id).where(
                    ConversationEvent.conversation_id.in_([old_id, fresh_id])
                )
            )
            remaining = set(events.scalars().all())
            rows = await session.execute(
                select(Conversation.id).where(Conversation.id.in_([old_id, fresh_id]))
            )
            return remaining, set(rows.scalars().all())

        remaining_events, remaining_calls = _run_db(check)

        assert cleared >= 1
        assert old_id not in remaining_events  # the transcript is gone
        assert fresh_id in remaining_events  # the one inside the period is untouched
        assert remaining_calls == {old_id, fresh_id}  # both calls still exist
    finally:

        async def clean(session: AsyncSession):
            await session.execute(
                delete(ConversationEvent).where(
                    ConversationEvent.conversation_id.in_([old_id, fresh_id])
                )
            )
            await session.execute(
                delete(Conversation).where(Conversation.id.in_([old_id, fresh_id]))
            )
            await session.commit()

        _run_db(clean)
