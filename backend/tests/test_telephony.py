"""Inbound calls reach the right agent, and only Twilio can make one happen.

The incoming-call webhook is a public URL with no session behind it. Everything here is about the
two ways that could go wrong: someone who is not Twilio starting calls on our providers' bill, and
a caller reaching an agent that is not theirs.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import telephony as telephony_api
from app.workloads.conversational.telephony import (
    connect_stream,
    read_stream_start,
    say_and_hang_up,
    signature_for,
    signature_valid,
)

TOKEN = "12345"
URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
PARAMS = {
    "Digits": "1234",
    "To": "+18005551212",
    "From": "+14158675310",
    "Caller": "+14158675310",
    "CallSid": "CA1234567890ABCDE",
}


def test_the_signature_matches_twilios_documented_algorithm() -> None:
    """Pinned against the string Twilio's security docs specify, cross-checked with openssl.

    Not a self-consistency test: the payload below is the exact concatenation Twilio documents —
    the called URL with each parameter's key immediately followed by its value, in key order.
    """
    expected_payload = (
        "https://mycompany.com/myapp.php?foo=1&bar=2"
        "CallSidCA1234567890ABCDECaller+14158675310Digits1234From+14158675310To+18005551212"
    )
    import base64
    import hashlib
    import hmac

    independent = base64.b64encode(
        hmac.new(TOKEN.encode(), expected_payload.encode(), hashlib.sha1).digest()
    ).decode()

    assert signature_for(URL, PARAMS, TOKEN) == independent


@pytest.mark.parametrize(
    "header",
    [None, "", "bogus", "GvWf1cFY/Q7PnoempGyD5oXAezd="],  # last: one character off
)
def test_anything_but_the_real_signature_is_rejected(header: str | None) -> None:
    assert signature_valid(URL, PARAMS, header, TOKEN) is False


def test_the_real_signature_is_accepted() -> None:
    assert signature_valid(URL, PARAMS, signature_for(URL, PARAMS, TOKEN), TOKEN) is True


def test_a_changed_parameter_invalidates_the_signature() -> None:
    """The signature covers the parameters, so a rewritten caller id must not verify."""
    signature = signature_for(URL, PARAMS, TOKEN)

    tampered = {**PARAMS, "From": "+10000000000"}

    assert signature_valid(URL, tampered, signature, TOKEN) is False


def test_twiml_connects_the_call_to_the_media_socket() -> None:
    """`<Connect>` is bidirectional; `<Start>` would only fork the audio and the agent'd be mute."""
    xml = connect_stream("wss://api.example.com/telephony/media")

    assert "<Connect>" in xml and "<Stream" in xml
    assert "wss://api.example.com/telephony/media" in xml


def test_custom_data_is_carried_by_parameter_not_by_query_string() -> None:
    """The bug that made every real call fail: Twilio drops the query string entirely."""
    xml = connect_stream("wss://api.example.com/telephony/media", token="s3cr3t")

    assert '<Parameter name="token" value="s3cr3t" />' in xml
    assert "?" not in xml.split("url=")[1].split(" ")[0]


def test_an_unavailable_number_says_something_rather_than_nothing() -> None:
    xml = say_and_hang_up("Nicht verfügbar")

    assert "<Say" in xml and "<Hangup" in xml and "Nicht verf" in xml


def test_twiml_escapes_text_so_a_message_cannot_break_the_document() -> None:
    xml = say_and_hang_up('Fritz & <b>Co</b> "Praxis"')

    assert "&amp;" in xml and "&lt;b&gt;" in xml
    assert "<b>" not in xml


def test_the_stream_sids_and_parameters_are_read_from_twilios_start_frame() -> None:
    """`start` carries the SIDs the serializer needs and the only custom data Twilio delivers."""
    import asyncio

    frames = [
        json.dumps({"event": "connected", "protocol": "Call"}),
        json.dumps(
            {
                "event": "start",
                "streamSid": "MZ123",
                "start": {
                    "streamSid": "MZ123",
                    "callSid": "CA999",
                    "customParameters": {"token": "abc123"},
                },
            }
        ),
    ]

    async def receive() -> str:
        return frames.pop(0)

    assert asyncio.run(read_stream_start(receive)) == ("MZ123", "CA999", {"token": "abc123"})


def test_a_stream_with_no_parameters_still_reads() -> None:
    """A stream started without <Parameter> children must not crash the handler."""
    import asyncio

    async def receive() -> str:
        return json.dumps({"event": "start", "start": {"streamSid": "MZ", "callSid": "CA"}})

    assert asyncio.run(read_stream_start(receive)) == ("MZ", "CA", {})


def test_a_stream_that_never_starts_is_given_up_on() -> None:
    """A peer that holds the socket open without starting must not hold it forever."""
    import asyncio

    async def receive() -> str:
        return json.dumps({"event": "media"})

    with pytest.raises(ValueError):
        asyncio.run(read_stream_start(receive, limit=3))


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(telephony_api.router, prefix="/telephony")
    return app


def test_the_webhook_refuses_to_answer_when_no_auth_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: an unsigned public webhook is worse than an unanswered call."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", None)

    response = TestClient(_app()).post("/telephony/incoming", data={"To": "+491234"})

    assert response.status_code == 503
    assert "TWILIO_AUTH_TOKEN" in response.json()["detail"]


def test_an_unsigned_request_is_refused_before_any_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody who merely found the URL gets to start a call on our providers' bill."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", TOKEN)
    looked_up: list[Any] = []

    async def spy(*args: Any, **kwargs: Any):
        looked_up.append(kwargs)
        return None

    monkeypatch.setattr(telephony_api.repo, "instance_for_phone_number", spy)

    response = TestClient(_app()).post("/telephony/incoming", data={"To": "+491234"})

    assert response.status_code == 403
    assert looked_up == [], "the number was resolved despite a failed signature check"


def test_a_forwarded_https_request_verifies_behind_the_proxy() -> None:
    """Railway terminates TLS, so request.url says http while Twilio signed https."""

    class _Req:
        def __init__(self) -> None:
            self.url = "http://api.example.com/telephony/incoming"
            self.headers = {"x-forwarded-proto": "https"}

    assert telephony_api._public_url(_Req()) == "https://api.example.com/telephony/incoming"


def test_an_unforwarded_request_keeps_its_scheme() -> None:
    class _Req:
        def __init__(self) -> None:
            self.url = "http://localhost:8000/telephony/incoming"
            self.headers: dict[str, str] = {}

    assert telephony_api._public_url(_Req()) == "http://localhost:8000/telephony/incoming"


def test_a_blank_parameter_is_kept_because_the_signature_covers_it() -> None:
    """Twilio signs every parameter it sends, including empty ones.

    Dropping a blank value (urlencoded parsing's default) would change the string being hashed and
    make a genuine call fail verification — a bug that would only appear for some callers.
    """
    from app.workloads.conversational.telephony import form_params

    parsed = form_params(b"To=%2B491234&From=&CallSid=CA1")

    assert parsed == {"To": "+491234", "From": "", "CallSid": "CA1"}


def test_a_correctly_signed_call_resolves_the_number_and_returns_a_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path end to end: signature verifies, the number resolves, the call is connected."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", TOKEN)
    instance_id, owner_id = uuid.uuid4(), uuid.uuid4()
    recorded: dict[str, Any] = {}

    class _Instance:
        id = instance_id
        tenant_id = owner_id
        param_values = {"locale": "en"}

    class _Conversation:
        id = uuid.uuid4()

    async def resolve(session: Any, *, phone_number: str):
        recorded["dialled"] = phone_number
        return _Instance()

    async def create(session: Any, **kwargs: Any):
        recorded.update(kwargs)
        return _Conversation()

    monkeypatch.setattr(telephony_api.repo, "instance_for_phone_number", resolve)
    monkeypatch.setattr(telephony_api.repo, "create_conversation", create)

    app = _app()
    app.dependency_overrides[telephony_api.get_session] = lambda: None
    client = TestClient(app)
    body = {"To": "+4915112345678", "From": "+4930999", "CallSid": "CA42"}
    url = "http://testserver/telephony/incoming"
    response = client.post(
        "/telephony/incoming", data=body, headers={"X-Twilio-Signature": signature_for(url, body, TOKEN)}
    )

    assert response.status_code == 200
    # The token must ride as a <Parameter>: Twilio connects to the bare url and drops any query
    # string, which is precisely what made real calls fail with a 403 handshake.
    assert "<Connect>" in response.text
    assert '<Parameter name="token"' in response.text
    assert "?token=" not in response.text
    assert recorded["dialled"] == "+4915112345678"
    # The call is recorded against the agent that owns the number, keyed by Twilio's own call id.
    assert recorded["instance_id"] == instance_id
    assert recorded["external_id"] == "CA42"
    assert recorded["channel"] == "phone" and recorded["direction"] == "inbound"
    # The agent speaks the language its instance was configured with, not a default.
    assert recorded["metadata"]["language"] == "en"


def test_an_unknown_number_is_answered_with_a_spoken_apology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller must never get dead air or a carrier error tone."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", TOKEN)

    async def resolve(session: Any, *, phone_number: str):
        return None

    monkeypatch.setattr(telephony_api.repo, "instance_for_phone_number", resolve)

    app = _app()
    app.dependency_overrides[telephony_api.get_session] = lambda: None
    body = {"To": "+499999", "CallSid": "CA1"}
    url = "http://testserver/telephony/incoming"
    response = TestClient(app).post(
        "/telephony/incoming", data=body, headers={"X-Twilio-Signature": signature_for(url, body, TOKEN)}
    )

    assert response.status_code == 200
    assert "<Say" in response.text and "<Hangup" in response.text
    assert "<Connect>" not in response.text
