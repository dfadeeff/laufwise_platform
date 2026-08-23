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

    @classmethod
    def of(cls, conversation) -> "ConversationSummary":
        return cls(
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
