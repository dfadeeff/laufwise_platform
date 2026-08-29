"""Write a live call down as it happens, so it can be read back afterwards.

A conversational agent is the one tier where a model decides what to say, and an unrecorded model
decision may as well not have happened. This records the timeline the read API at `/conversations`
was built for: every caller turn, every agent turn, every tool call with its arguments and its
result, and the id of any governed run the call produced.

Two deliberate asymmetries:

- Starting a call needs a conversation row. That happens in the HTTP request BEFORE any audio, so
  a failure there is surfaced as an error the caller sees rather than a call nobody can account for.
- Appending an event is best effort. A database blip mid-call must not cut off a person who is
  mid-sentence, so a failed write is logged and dropped. Losing a turn is bad; dropping the call to
  avoid losing a turn is worse.

Each write opens its own short session: the recorder outlives any request, and the engine's
NullPool hands out a fresh connection per checkout anyway.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.db import repo
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)


class ConversationRecorder:
    """Appends one call's timeline. Safe to call from inside a running pipeline."""

    def __init__(self, conversation_id: uuid.UUID) -> None:
        self.conversation_id = conversation_id

    async def turn(self, role: str, text: str) -> None:
        """One side of the conversation, as it was actually said."""
        if text.strip():
            await self._append("turn", {"role": role, "text": text.strip()})

    async def tool(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        """A tool call with what went in and what came back.

        The result matters more than the transcript: it is how you tell an agent that booked an
        appointment from one that only said it did.
        """
        payload: dict[str, Any] = {"tool": name, "arguments": arguments, "result": result}
        if run_id := result.get("run_id"):
            # Links the call to its governed run, so the engine's ruling and the sentence the
            # caller heard can be read side by side.
            payload["run_id"] = run_id
        await self._append("tool_call", payload)

    async def finish(self, status: str = "completed") -> None:
        try:
            async with get_sessionmaker()() as session:
                await repo.end_conversation(
                    session, conversation_id=self.conversation_id, status=status
                )
        except Exception:  # noqa: BLE001 — see module docstring: never break a call to log one
            log.exception("could not close conversation %s", self.conversation_id)

    async def _append(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            async with get_sessionmaker()() as session:
                await repo.append_conversation_event(
                    session, conversation_id=self.conversation_id, kind=kind, payload=payload
                )
        except Exception:  # noqa: BLE001
            log.exception("could not record %s on conversation %s", kind, self.conversation_id)
