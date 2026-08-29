"""Authenticated Studio entry point and short-lived media WebSocket."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel

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
from app.workloads.conversational.sessions import studio_voice_sessions
from app.workloads.conversational.surface import run_studio_session

# The template the Studio voice tester runs as. A call is stored against a deployed instance of
# it, which is what pins a saved transcript to the agent version that produced it.
STUDIO_TEMPLATE = "voice_appointment"

router = APIRouter()


class StudioVoiceSessionRequest(BaseModel):
    language: Literal["de", "en", "ar"] = "de"


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
    missing = [
        name
        for name, value in (
            ("DEEPGRAM_API_KEY", settings.deepgram_api_key),
            ("OPENAI_API_KEY", settings.openai_api_key),
            ("ELEVENLABS_API_KEY", settings.elevenlabs_api_key),
            ("ELEVENLABS_VOICE_ID", settings.elevenlabs_voice_id),
        )
        if not value
    ]
    if missing:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"conversational surface is not configured: {', '.join(missing)}",
        )
    # Resolve the instance and open the conversation BEFORE any audio flows. Failing here is a
    # readable error on an HTTP request; failing mid-call would leave a conversation nobody can
    # account for, which is the thing this is meant to prevent.
    instance = await repo.studio_voice_instance(
        session, tenant_id=tenant.id, template_name=STUDIO_TEMPLATE
    )
    if instance is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{STUDIO_TEMPLATE} is not published yet — no agent to hold the conversation",
        )
    conversation = await repo.create_conversation(
        session,
        tenant_id=tenant.id,
        instance_id=instance.id,
        channel="voice",
        direction="inbound",
        metadata={"surface": "studio", "language": selection.language},
    )
    token = studio_voice_sessions.create(
        str(tenant.id), selection.language, conversation_id=conversation.id
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
        session = studio_voice_sessions.authorize(token)
    except KeyError:
        await websocket.close(code=1008, reason="invalid or expired voice token")
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
    )
