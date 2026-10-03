"""Authenticated Studio entry point and short-lived media WebSocket."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, model_validator

from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.workloads.conversational.recording import ConversationRecorder
from app.agents.runtime import admit_voice_call, open_voice_call, prepare_voice
from app.agents import service
from app.workloads.conversational.surface import missing_voice_keys, uses_realtime
from app.workloads.conversational.surface import run_studio_session

# The template the Studio voice tester runs as. A call is stored against a deployed instance of
# it, which is what pins a saved transcript to the agent version that produced it.
STUDIO_TEMPLATE = "voice_appointment"

log = logging.getLogger(__name__)

router = APIRouter()


class StudioVoiceSessionRequest(BaseModel):
    language: Literal["de", "en", "ru", "ar"] = "de"
    agent_id: str | None = None
    generation: int | None = None
    # Which calendar a test call uses (ADR-0018). "sandbox" never touches a real system; "read"
    # reads the practice's real calendar and stops before any write; "write" books for real.
    calendar_mode: Literal["sandbox", "read", "write"] = "sandbox"
    connection_id: str | None = None
    # Writing real appointments from a test is a decision, so it is never a default.
    confirm_real_writes: bool = False

    @model_validator(mode="after")
    def real_calendar_needs_an_account_and_writes_need_a_yes(self):
        if self.calendar_mode != "sandbox" and not self.connection_id:
            raise ValueError("Choose the calendar account to test against.")
        if self.calendar_mode == "write" and not self.confirm_real_writes:
            raise ValueError(
                "Writing test appointments into the real calendar needs you to confirm it."
            )
        return self


def websocket_url(http_url: str, *, secure: bool) -> str:
    """Translate an externally visible HTTP URL without trusting the proxy's internal scheme."""
    parts = urlsplit(http_url)
    return urlunsplit(("wss" if secure else "ws", parts.netloc, parts.path, parts.query, parts.fragment))


@router.post("/sessions")
async def create_studio_session(
    request: Request,
    selection: StudioVoiceSessionRequest,
    tenant: Tenant = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    # Resolve the instance and open the conversation BEFORE any audio flows. Failing here is a
    # readable error on an HTTP request; failing mid-call would leave a conversation nobody can
    # account for, which is the thing this is meant to prevent.
    if selection.agent_id:
        agent = await service.get_agent(session, tenant.id, selection.agent_id, lock=True)
        service.check_generation(agent, selection.generation)
        instance = await service.make_snapshot(session, agent, kind="test")
        await session.commit()
    else:
        instance = await repo.studio_voice_instance(
            session, tenant_id=tenant.id, template_name=STUDIO_TEMPLATE
        )
    if instance is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{STUDIO_TEMPLATE} is not published yet — no agent to hold the conversation",
        )
    # A test call uses the sandbox unless the tester chose the practice's real calendar
    # (ADR-0018). Then the account must be one of this practice's voice-capable connections, and
    # the calendar is built exactly as a live call would build it.
    connection = None
    if selection.calendar_mode != "sandbox":
        if not selection.agent_id:
            raise HTTPException(422, "Testing on the real calendar needs an agent.")
        connection = await service.owned_connection(session, tenant.id, selection.connection_id)
    calendar, calendar_kind, config = await prepare_voice(
        session, instance, rehearsal=True, calendar_mode=selection.calendar_mode,
        connection=connection,
    )
    if calendar_kind != "sandbox" and hasattr(calendar, "close"):
        # Built here only to prove it can be; the socket builds its own for the call.
        calendar.close()
    # Only the vendors this agent's engine actually uses: a realtime agent needs no Deepgram or
    # ElevenLabs key, and refusing it one would block a call that would have worked.
    missing = missing_voice_keys(config, selection.language)
    if missing:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"conversational surface is not configured: {', '.join(missing)}",
        )
    conversation = await repo.create_conversation(
        session,
        tenant_id=tenant.id,
        instance_id=instance.id,
        channel="voice",
        direction="inbound",
        metadata={
            "surface": "studio",
            "language": selection.language,
            "calendar": calendar_kind, "mode": "rehearsal",
            "calendar_mode": selection.calendar_mode,
            "engine": "realtime" if uses_realtime(config) else "cascaded",
            "agent_id": selection.agent_id, "revision": getattr(instance, "revision", None),
        },
    )
    token = await admit_voice_call(
        session, conversation.id, language=selection.language, rehearsal=True,
        calendar_mode=selection.calendar_mode,
        connection_id=connection.id if connection is not None else None,
    )
    # Railway terminates TLS before forwarding to uvicorn, so request.url may say http even when
    # the browser reached the API over HTTPS. Returning ws:// to an HTTPS page is blocked by every
    # browser. Production is always secure; X-Forwarded-Proto also covers other TLS proxies.
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    origin = request.headers.get("origin", "")
    secure = (
        settings.app_env == "production"
        or forwarded_proto == "https"
        or origin.startswith("https://")
    )
    media_url = websocket_url(str(request.url_for("studio_voice_websocket")), secure=secure)
    # The id is returned so the Studio can link straight to the saved call afterwards.
    return {"ws_url": f"{media_url}?token={token}", "conversation_id": conversation.id.hex}


@router.websocket("/ws", name="studio_voice_websocket")
async def studio_voice_websocket(websocket: WebSocket, token: str) -> None:
    # Accept BEFORE authorizing. Closing a WebSocket that was never accepted makes Starlette
    # reject the handshake with HTTP 403, and the browser reports an abnormal 1006 with no reason
    # — indistinguishable from a network failure. Accepting first costs nothing (no pipeline, no
    # provider is reached) and lets a rejected token arrive as a readable 1008.
    await websocket.accept()
    try:
        session = await open_voice_call(token)
    except KeyError:
        await websocket.close(code=1008, reason="invalid or expired voice token")
        return
    except Exception:  # noqa: BLE001 — the tester gets a reason, we keep the stack trace
        log.exception("could not open the Studio voice session")
        await websocket.close(code=1011, reason="the call could not be prepared")
        return
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=ProtobufFrameSerializer(),
        ),
    )
    await run_studio_session(
        transport,
        language=session.language,
        recorder=ConversationRecorder(session.conversation_id),
        base_prompt=session.base_prompt,
        calendar=session.calendar, config=session.config, contracts=session.contracts, rehearsal=True,
        knowledge=session.knowledge,
        # Every Studio test says which calendar it uses, so a sandbox booking is called a test.
        test_mode=session.calendar_mode,
    )
