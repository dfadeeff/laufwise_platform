"""Authenticated Studio entry point and short-lived media WebSocket."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status

from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app.api.deps import current_tenant
from app.config import settings
from app.db.models import Tenant
from app.workloads.conversational.sessions import studio_voice_sessions
from app.workloads.conversational.surface import run_studio_session

router = APIRouter()


@router.post("/sessions")
async def create_studio_session(
    request: Request, tenant: Tenant = Depends(current_tenant)
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
    token = studio_voice_sessions.create(str(tenant.id))
    media_url = str(request.url_for("studio_voice_websocket"))
    media_url = media_url.replace("https://", "wss://").replace("http://", "ws://")
    return {"ws_url": f"{media_url}?token={token}"}


@router.websocket("/ws", name="studio_voice_websocket")
async def studio_voice_websocket(websocket: WebSocket, token: str) -> None:
    try:
        studio_voice_sessions.authorize(token)
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
    await run_studio_session(transport)
