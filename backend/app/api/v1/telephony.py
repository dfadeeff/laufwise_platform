"""Inbound telephony — a phone call reaches the same agent the Studio talks to.

Two endpoints, mirroring the Studio's pair. Twilio fetches TwiML when a call arrives; we resolve
which agent answers that number, open its conversation, and hand back a `<Connect><Stream>`
pointing at the media socket. The socket then runs the ordinary pipeline over Twilio's audio.

The webhook is a PUBLIC url with no session behind it, so it fails closed twice: it refuses to
answer at all without a configured auth token, and rejects any request whose Twilio signature does
not verify. Without that, anyone who found the url could start sessions on our providers' bill.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app.api.v1.conversational import websocket_url
from app.config import settings
from app.db import repo
from app.db.session import get_session
from app.workloads.conversational.recording import ConversationRecorder
from app.workloads.conversational.sessions import VoiceLanguage, voice_sessions
from app.workloads.conversational.surface import run_studio_session
from app.workloads.conversational.telephony import (
    connect_stream,
    form_params,
    read_stream_start,
    say_and_hang_up,
    signature_valid,
)

router = APIRouter()

TWIML = "application/xml"

# Twilio's audio is 8 kHz mu-law in both directions; the serializer converts, the pipeline runs
# at its own rate. Saying so explicitly keeps the transport from guessing.
TWILIO_SAMPLE_RATE = 8000

_UNAVAILABLE = {
    "de": "Diese Nummer ist im Moment nicht verfügbar. Bitte versuchen Sie es später erneut.",
    "en": "This number is not available right now. Please try again later.",
}


def _public_url(request: Request) -> str:
    """The URL Twilio actually called.

    The signature covers it character for character, and Railway terminates TLS before uvicorn —
    so `request.url` says http even though Twilio called https. Rebuilding from the forwarded
    scheme is what makes verification work behind the proxy.
    """
    url = str(request.url)
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if forwarded == "https" and url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


@router.post("/incoming")
async def incoming_call(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Answer an inbound call by connecting it to the agent that owns the dialled number."""
    if not settings.twilio_auth_token:
        # Fail closed: an unsigned public webhook is worse than an unanswered one.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "telephony is not configured: TWILIO_AUTH_TOKEN"
        )
    form = form_params(await request.body())
    if not signature_valid(
        _public_url(request),
        form,
        request.headers.get("x-twilio-signature"),
        settings.twilio_auth_token,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid Twilio signature")

    dialled = form.get("To", "")
    instance = await repo.instance_for_phone_number(session, phone_number=dialled)
    if instance is None:
        # A caller must hear a sentence, never dead air or a Twilio error tone.
        return Response(say_and_hang_up(_UNAVAILABLE["de"]), media_type=TWIML)

    language: VoiceLanguage = instance.param_values.get("locale", "de")
    conversation = await repo.create_conversation(
        session,
        tenant_id=instance.tenant_id,
        instance_id=instance.id,
        channel="phone",
        direction="inbound",
        # The Twilio CallSid, so a recorded call can be reconciled with the carrier's own record.
        external_id=form.get("CallSid"),
        metadata={"surface": "telephony", "language": language, "from": form.get("From", "")},
    )
    token = voice_sessions.create(
        str(instance.tenant_id), language, conversation_id=conversation.id
    )
    # Always wss: Twilio Media Streams refuses a plaintext ws:// url, and Twilio can never reach
    # a local dev host anyway, so there is no case where the insecure scheme is the right answer.
    media = websocket_url(str(request.url_for("telephony_media_websocket")), secure=True)
    # The token is a <Parameter>, not a query string — Twilio connects to the bare url.
    return Response(connect_stream(media, token=token), media_type=TWIML)


@router.websocket("/media", name="telephony_media_websocket")
async def telephony_media_websocket(websocket: WebSocket, token: str | None = None) -> None:
    """Run one phone call. Same agent, same governed booking — only the wire is different.

    `token` stays an OPTIONAL query parameter. Twilio never sends one — it connects to the bare
    url and delivers `<Parameter>` values in the start frame — and declaring it required made
    FastAPI answer Twilio's handshake with a 403, which the caller heard as silence. Keeping the
    query form working as well costs nothing and lets a browser or a test client connect directly.
    """
    await websocket.accept()

    # Read the opening frames first: they carry the SIDs the serializer needs AND, for a Twilio
    # call, the token itself. Nothing can be authorized before this.
    try:
        stream_sid, call_sid, custom = await read_stream_start(websocket.receive_text)
    except (ValueError, KeyError):
        await websocket.close(code=1008, reason="no Twilio start event")
        return

    try:
        session = voice_sessions.authorize(token or custom.get("token", ""))
    except KeyError:
        await websocket.close(code=1008, reason="invalid or expired call token")
        return

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        # Hanging up needs the REST credentials; without them the call would end only when the
        # caller does, so the capability is switched off rather than failing at construction.
        params=TwilioFrameSerializer.InputParams(
            auto_hang_up=bool(settings.twilio_account_sid and settings.twilio_auth_token)
        ),
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=TWILIO_SAMPLE_RATE,
            audio_out_sample_rate=TWILIO_SAMPLE_RATE,
            add_wav_header=False,
            serializer=serializer,
        ),
    )
    await run_studio_session(
        transport,
        language=session.language,
        recorder=ConversationRecorder(session.conversation_id),
    )
