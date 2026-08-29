"""Read models for real-time human sessions and their ordered surface events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ConversationEventOut(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


def _outcome(events) -> str | None:
    """What the engine ruled on this call's last consequential action, if it had one.

    Read off the governed run rather than off a tool name: every tier's consequential action
    compiles to a run, so this stays true for any conversational agent, not just the booking one.
    A call with no `run_id` anywhere never attempted anything consequential, and reports None
    rather than a misleading "ok".
    """
    for event in reversed(list(events)):
        if event.kind == "tool_call" and event.payload.get("run_id"):
            status = event.payload.get("result", {}).get("status")
            return str(status) if status else None
    return None


def _opening(events) -> str | None:
    """The caller's first words — what the call was about, before anyone acted on it.

    A list of calls needs a line that distinguishes one from another; timestamps and turn counts
    do not. This is the closest thing a conversation has to a subject line.
    """
    for event in events:
        if event.kind == "turn" and event.payload.get("role") == "caller":
            text = str(event.payload.get("text", "")).strip()
            return text or None
    return None


class ConversationSummary(BaseModel):
    conversation_id: str
    instance_id: str
    channel: str
    direction: str
    status: str
    external_id: str | None
    metadata: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None
    # Enough to triage a list of calls without opening each one: how much was said, how much the
    # agent did, and how the engine ruled on it.
    turns: int
    tool_calls: int
    outcome: str | None
    opening: str | None

    @classmethod
    def of(cls, conversation) -> "ConversationSummary":
        events = list(conversation.events)
        return cls(
            turns=sum(1 for event in events if event.kind == "turn"),
            tool_calls=sum(1 for event in events if event.kind == "tool_call"),
            outcome=_outcome(events),
            opening=_opening(events),
            conversation_id=conversation.id.hex,
            instance_id=conversation.instance_id.hex,
            channel=conversation.channel,
            direction=conversation.direction,
            status=conversation.status,
            external_id=conversation.external_id,
            metadata=conversation.metadata_,
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
        )


class ConversationDetail(ConversationSummary):
    events: list[ConversationEventOut]

    @classmethod
    def of(cls, conversation) -> "ConversationDetail":
        summary = ConversationSummary.of(conversation)
        return cls(
            **summary.model_dump(),
            events=[
                ConversationEventOut(
                    seq=event.seq,
                    kind=event.kind,
                    payload=event.payload,
                    created_at=event.created_at,
                )
                for event in conversation.events
            ],
        )
