"""Twilio Media Streams — the phone as one more transport for the same agent.

A call is the same conversation the Studio already holds; only the wire differs. Twilio answers
an inbound call by fetching TwiML from us, we tell it to open a WebSocket, and 8 kHz mu-law audio
flows over that socket. Pipecat's `TwilioFrameSerializer` speaks that protocol, so the agent, its
prompt, its tools and its governed booking are reused untouched — this module is the adapter, and
nothing below it knows a telephone exists.

Everything here is protocol, not HTTP: signature checking, TwiML, and reading the stream's opening
handshake. The router stays thin (CLAUDE.md §0).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr


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


def connect_stream(ws_url: str) -> str:
    """TwiML that hands the call's audio to our media socket.

    `<Connect><Stream>` is bidirectional — the agent can speak back — unlike `<Start><Stream>`,
    which only forks the audio to a listener.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(ws_url)} /></Connect></Response>"
    )


def say_and_hang_up(message: str, language: str = "de-DE") -> str:
    """TwiML for a call we cannot take. A caller should hear a sentence, never dead air."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say language={quoteattr(language)}>{escape(message)}</Say>"
        "<Hangup /></Response>"
    )


async def read_stream_start(receive_text, *, limit: int = 5) -> tuple[str, str | None]:
    """Consume Twilio's opening frames and return `(stream_sid, call_sid)`.

    Twilio sends `connected` and then `start`; the SIDs the serializer needs only exist in
    `start`, so the socket has to be read before the pipeline can be built. `limit` stops a
    peer that never sends one from holding the connection open.
    """
    for _ in range(limit):
        message = json.loads(await receive_text())
        if message.get("event") == "start":
            start = message.get("start", {})
            return start.get("streamSid") or message.get("streamSid", ""), start.get("callSid")
    raise ValueError("Twilio media stream sent no start event")
