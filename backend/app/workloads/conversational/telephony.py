"""Twilio Media Streams — the phone as one more transport for the same agent.

A call is the same conversation the Studio already holds; only the wire differs. Twilio answers
an inbound call by fetching TwiML from us, we tell it to open a WebSocket, and 8 kHz mu-law audio
flows over that socket. Pipecat's `TwilioFrameSerializer` speaks that protocol, so the agent, its
prompt, its tools and its governed booking are reused untouched — this module is the adapter, and
nothing below it knows a telephone exists.

Everything here is protocol, not HTTP: signature checking, TwiML, reading the stream's opening
handshake, and handing a live call to a person. The router stays thin (CLAUDE.md §0).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr

import httpx
from pipecat.frames.frames import CancelFrame, EndFrame, Frame
from pipecat.serializers.twilio import TwilioFrameSerializer

# How long the practice's phone rings before the caller is told nobody answered.
TRANSFER_RING_SECONDS = 25


def form_params(body: bytes) -> dict[str, str]:
    """Twilio's urlencoded webhook body.

    Parsed here rather than through Starlette's `request.form()`, which pulls in python-multipart
    for a content type Twilio never sends. `keep_blank_values` is not optional: an empty parameter
    still counts toward the signature, and dropping it would make a genuine request fail to verify.
    """
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))


def signature_for(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio's request signature: the URL with sorted form params appended, HMAC-SHA1, base64.

    Twilio concatenates each key immediately followed by its value, in alphabetical key order,
    onto the exact URL it called — so the URL we rebuild has to match theirs character for
    character, including scheme and query string.
    """
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def signature_valid(url: str, params: dict[str, str], header: str | None, auth_token: str) -> bool:
    """Whether a webhook really came from Twilio. Compared in constant time."""
    if not header:
        return False
    return hmac.compare_digest(signature_for(url, params, auth_token), header)


def connect_stream(ws_url: str, **parameters: str) -> str:
    """TwiML that hands the call's audio to our media socket.

    `<Connect><Stream>` is bidirectional — the agent can speak back — unlike `<Start><Stream>`,
    which only forks the audio to a listener.

    Anything the socket needs to know rides as a `<Parameter>`, never in the URL's query string:
    Twilio connects to the bare url and drops the query, so a token passed that way simply never
    arrives and the handshake fails with a 403 the caller experiences as dead air. Twilio delivers
    these in the start frame's `customParameters` instead.
    """
    extras = "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)} />"
        for name, value in parameters.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(ws_url)}>{extras}</Stream></Connect></Response>"
    )


def say_and_hang_up(message: str, language: str = "de-DE") -> str:
    """TwiML for a call we cannot take. A caller should hear a sentence, never dead air."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say language={quoteattr(language)}>{escape(message)}</Say>"
        "<Hangup /></Response>"
    )


def transfer_to(number: str, unanswered: str, language: str = "de-DE") -> str:
    """TwiML that puts the caller through to a person, and says so if nobody picks up.

    `<Dial>` without a `callerId` shows the practice the caller's own number, which is the number
    they would call back.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial timeout="{TRANSFER_RING_SECONDS}">{escape(number)}</Dial>'
        f"<Say language={quoteattr(language)}>{escape(unanswered)}</Say>"
        "<Hangup /></Response>"
    )


async def redirect_call(
    account_sid: str,
    auth_token: str,
    call_sid: str,
    twiml: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Replace a live call's TwiML. Twilio ends the media stream and runs the new document.

    Raises on anything but success: a transfer Twilio refused must never read as one that worked.
    """
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
    if client is None:
        async with httpx.AsyncClient(timeout=10) as owned:
            response = await owned.post(url, data={"Twiml": twiml}, auth=(account_sid, auth_token))
    else:
        response = await client.post(url, data={"Twiml": twiml}, auth=(account_sid, auth_token))
    response.raise_for_status()


_TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"


async def account_numbers(
    account_sid: str, auth_token: str, *, client: httpx.AsyncClient | None = None
) -> list[dict]:
    """The platform account's phone numbers, as Twilio describes them (sid, capabilities, voice_url).

    One page of 1000 is the whole account at any size this platform will reach; a pool that needs
    paging is a pool that needs a different design.
    """
    url = f"{_TWILIO_API}/{account_sid}/IncomingPhoneNumbers.json"
    params = {"PageSize": "1000"}
    if client is None:
        async with httpx.AsyncClient(timeout=10) as owned:
            response = await owned.get(url, params=params, auth=(account_sid, auth_token))
    else:
        response = await client.get(url, params=params, auth=(account_sid, auth_token))
    response.raise_for_status()
    return list(response.json().get("incoming_phone_numbers") or [])


async def point_voice_at(
    account_sid: str,
    auth_token: str,
    number_sid: str,
    webhook_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Send a number's incoming calls to our webhook. What used to be a step in the Twilio console."""
    url = f"{_TWILIO_API}/{account_sid}/IncomingPhoneNumbers/{number_sid}.json"
    data = {"VoiceUrl": webhook_url, "VoiceMethod": "POST"}
    if client is None:
        async with httpx.AsyncClient(timeout=10) as owned:
            response = await owned.post(url, data=data, auth=(account_sid, auth_token))
    else:
        response = await client.post(url, data=data, auth=(account_sid, auth_token))
    response.raise_for_status()


class HandOffSerializer(TwilioFrameSerializer):
    """Twilio's serializer, minus the hang-up once the call belongs to someone else.

    Pipecat ends the carrier call over REST when the pipeline stops. After a transfer the pipeline
    stops *because* Twilio moved the call on, and that hang-up would cut the caller off while the
    practice's phone is ringing.
    """

    _handed_off = False

    def hand_off(self) -> None:
        self._handed_off = True

    def take_back(self) -> None:
        """The transfer did not happen, so the call is ours to end again."""
        self._handed_off = False

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if self._handed_off and isinstance(frame, (EndFrame, CancelFrame)):
            return None
        return await super().serialize(frame)


# What the caller hears when the practice's phone rings out. Spoken by Twilio, not the agent: by
# then the agent has handed the call over and is gone.
UNANSWERED = {
    "de": "Leider ist gerade niemand erreichbar. Bitte versuchen Sie es später noch einmal.",
    "en": "Sorry, nobody is available right now. Please try again later.",
    "ru": "К сожалению, сейчас никто не может ответить. Пожалуйста, перезвоните позже.",
    "ar": "عذرًا، لا يوجد أحد متاح الآن. يرجى المحاولة لاحقًا.",
}
SAY_LANGUAGE = {"de": "de-DE", "en": "en-US", "ru": "ru-RU", "ar": "arb"}


def transferring(serializer: HandOffSerializer, *, account_sid: str, auth_token: str, call_sid: str):
    """The phone channel's way of putting a caller through to a person.

    The serializer lets go of the call BEFORE the redirect: Twilio closes the media stream as soon
    as it accepts it, and the pipeline stopping must not hang up the call it was just given.
    """

    async def transfer(number: str, language: str) -> None:
        serializer.hand_off()
        try:
            await redirect_call(
                account_sid,
                auth_token,
                call_sid,
                transfer_to(number, UNANSWERED[language], SAY_LANGUAGE[language]),
            )
        except Exception:
            serializer.take_back()
            raise

    return transfer


async def read_stream_start(
    receive_text, *, limit: int = 5
) -> tuple[str, str | None, dict[str, str]]:
    """Consume Twilio's opening frames and return `(stream_sid, call_sid, custom_parameters)`.

    Twilio sends `connected` and then `start`; the SIDs the serializer needs — and the
    `<Parameter>` values, which are the only way custom data reaches this socket — exist solely in
    `start`, so the socket has to be read before anything else can happen. `limit` stops a peer
    that never sends one from holding the connection open.
    """
    for _ in range(limit):
        message = json.loads(await receive_text())
        if message.get("event") == "start":
            start = message.get("start", {})
            custom = {str(k): str(v) for k, v in (start.get("customParameters") or {}).items()}
            return (
                start.get("streamSid") or message.get("streamSid", ""),
                start.get("callSid"),
                custom,
            )
    raise ValueError("Twilio media stream sent no start event")
