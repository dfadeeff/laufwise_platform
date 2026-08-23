"""Authenticated Studio entry point and short-lived media WebSocket."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

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


def websocket_url(http_url: str, *, secure: bool) -> str:
    """Translate an externally visible HTTP URL without trusting the proxy's internal scheme."""
    parts = urlsplit(http_url)
    return urlunsplit(("wss" if secure else "ws", parts.netloc, parts.path, parts.query, parts.fragment))


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
    return {"ws_url": f"{media_url}?token={token}"}


@router.websocket("/ws", name="studio_voice_websocket")
async def studio_voice_websocket(websocket: WebSocket, token: str) -> None:
    try:
        studio_voice_sessions.authorize(token)
    except KeyError:
        await websocket.close(code=1008, reason="invalid or expired voice token")
        return
    # FastAPI does not accept WebSockets automatically. Pipecat's transport wraps an already
    # established socket; without this handshake browsers see an abnormal 1006 close before the
    # audio pipeline or any provider is reached.
    await websocket.accept()
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=ProtobufFrameSerializer(),
        ),
    )
    await run_studio_session(transport)
