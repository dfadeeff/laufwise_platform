"""Inbound calls reach the right agent, and only Twilio can make one happen.

The incoming-call webhook is a public URL with no session behind it. Everything here is about the
two ways that could go wrong: someone who is not Twilio starting calls on our providers' bill, and
a caller reaching an agent that is not theirs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import telephony as telephony_api
from app.workloads.conversational.telephony import (
    HandOffSerializer,
    connect_stream,
    redirect_call,
    read_stream_start,
    say_and_hang_up,
    signature_for,
    signature_valid,
    transfer_to,
)

async def _sandbox_calendar(_session, _instance, *, rehearsal=False):
    """Stand in for the connection lookup: an instance with no bound calendar books in memory."""
    from app.providers.sandbox import SandboxCalendar

    from app.agents.config import AgentConfig
    assert rehearsal is False
    return SandboxCalendar(), "thevea", AgentConfig()


def _configure_vendors(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(telephony_api.settings, name, "configured")


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
    _configure_vendors(monkeypatch)
    instance_id, owner_id = uuid.uuid4(), uuid.uuid4()
    recorded: dict[str, Any] = {}

    class _Instance:
        id = instance_id
        tenant_id = owner_id
        param_values = {"locale": "en"}
        runtime_config = {}

    class _Conversation:
        id = uuid.uuid4()

    async def resolve(session: Any, phone_number: str):
        recorded["dialled"] = phone_number
        return _Instance(), True

    async def create(session: Any, **kwargs: Any):
        recorded.update(kwargs)
        return _Conversation()

    monkeypatch.setattr(telephony_api.agent_store, "phone_instance", resolve)
    monkeypatch.setattr(telephony_api.repo, "create_conversation", create)
    # The unit transport double represents a resolved real calendar. Runtime isolation is
    # tested separately; a production call may no longer fall back to rehearsal.
    monkeypatch.setattr(
        telephony_api, "prepare_voice", _sandbox_calendar, raising=True
    )

    async def admit(session: Any, conversation_id: Any, **kwargs: Any) -> str:
        recorded["admitted"] = kwargs
        return "admitted-token"

    monkeypatch.setattr(telephony_api, "admit_voice_call", admit)

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
    assert recorded["admitted"]["rehearsal"] is False


def test_an_unknown_number_is_answered_with_a_spoken_apology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller must never get dead air or a carrier error tone."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", TOKEN)

    async def resolve(session: Any, phone_number: str):
        return None, True

    monkeypatch.setattr(telephony_api.agent_store, "phone_instance", resolve)

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


def test_a_call_with_a_vendor_key_missing_hears_a_sentence_not_dead_air(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a Deepgram key the pipeline fails only after Twilio has opened the media socket,
    and the caller hears silence. The webhook can see the gap before the call is connected, so it
    says so and opens no conversation."""
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", TOKEN)
    _configure_vendors(monkeypatch)
    monkeypatch.setattr(telephony_api.settings, "deepgram_api_key", None)
    opened: list[Any] = []

    class _Instance:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        param_values = {"locale": "de"}
        runtime_config = {}

    async def resolve(session: Any, phone_number: str):
        return _Instance(), True

    async def create(session: Any, **kwargs: Any):
        opened.append(kwargs)

    monkeypatch.setattr(telephony_api.agent_store, "phone_instance", resolve)
    monkeypatch.setattr(telephony_api.repo, "create_conversation", create)
    monkeypatch.setattr(telephony_api, "prepare_voice", _sandbox_calendar, raising=True)

    app = _app()
    app.dependency_overrides[telephony_api.get_session] = lambda: None
    body = {"To": "+4915112345678", "CallSid": "CA7"}
    url = "http://testserver/telephony/incoming"
    response = TestClient(app).post(
        "/telephony/incoming", data=body, headers={"X-Twilio-Signature": signature_for(url, body, TOKEN)}
    )

    assert response.status_code == 200
    assert "<Say" in response.text and "<Hangup" in response.text
    assert "<Connect>" not in response.text
    assert opened == []


# --- putting a caller through to a person ------------------------------------------------------


def test_a_transfer_dials_the_practice_and_says_something_if_nobody_answers() -> None:
    twiml = transfer_to("+4989123456", "Leider ist gerade niemand erreichbar.", "de-DE")

    assert '<Dial timeout="25">+4989123456</Dial>' in twiml
    assert twiml.index("<Dial") < twiml.index("<Say") < twiml.index("<Hangup")
    assert "Leider ist gerade niemand erreichbar." in twiml


def test_a_handed_off_call_is_not_hung_up_when_the_pipeline_stops() -> None:
    """After a transfer Twilio closes the media stream, the pipeline cancels, and Pipecat's
    serializer would end the whole call over REST, including the leg now ringing the
    practice. Once the call is handed off, ending the pipeline must leave the call alone."""
    from pipecat.frames.frames import CancelFrame, EndFrame

    hung_up: list[bool] = []

    def serializer() -> HandOffSerializer:
        s = HandOffSerializer(
            stream_sid="MZ1", call_sid="CA1", account_sid="AC1", auth_token="secret"
        )

        async def hang_up() -> None:
            hung_up.append(True)

        s._hang_up_call = hang_up  # type: ignore[method-assign]
        return s

    async def main() -> None:
        live = serializer()
        await live.serialize(EndFrame())
        assert hung_up == [True]

        handed_off = serializer()
        handed_off.hand_off()
        await handed_off.serialize(CancelFrame())
        await handed_off.serialize(EndFrame())

    asyncio.run(main())

    assert hung_up == [True]


def test_redirecting_a_live_call_posts_its_new_twiml_to_twilio() -> None:
    import httpx

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"sid": "CA1"})

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await redirect_call("AC1", "secret", "CA1", "<Response />", client=client)

    asyncio.run(main())

    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1/Calls/CA1.json"
    assert seen["auth"].startswith("Basic ")
    assert seen["body"] == {"Twiml": ["<Response />"]}


def test_a_redirect_twilio_refuses_raises_rather_than_claiming_a_transfer() -> None:
    import httpx

    async def main() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            await redirect_call("AC1", "secret", "CA1", "<Response />", client=client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(main())


def test_a_refused_transfer_gives_the_call_back_to_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Twilio refuses the redirect the caller is still talking to the agent, so ending the
    pipeline later must hang up as normal again."""
    from app.workloads.conversational import telephony

    async def refused(*_args, **_kwargs) -> None:
        raise RuntimeError("twilio refused")

    monkeypatch.setattr(telephony, "redirect_call", refused)
    serializer = HandOffSerializer(
        stream_sid="MZ1", call_sid="CA1", account_sid="AC1", auth_token="secret"
    )
    transfer = telephony.transferring(
        serializer, account_sid="AC1", auth_token="secret", call_sid="CA1"
    )

    with pytest.raises(RuntimeError):
        asyncio.run(transfer("+4989123456", "ru"))

    assert serializer._handed_off is False


def test_the_unanswered_sentence_exists_in_every_language_the_agent_speaks() -> None:
    from app.workloads.conversational import telephony

    for language in ("de", "en", "ru", "ar"):
        assert telephony.UNANSWERED[language] and telephony.SAY_LANGUAGE[language]


@pytest.mark.parametrize("credentials, offered", [(("AC1", TOKEN), True), ((None, None), False)])
def test_a_phone_call_is_given_a_way_to_reach_a_person_only_with_twilio_credentials(
    monkeypatch: pytest.MonkeyPatch, credentials: tuple, offered: bool
) -> None:
    from app.workloads.conversational.sessions import VoiceSession

    monkeypatch.setattr(telephony_api.settings, "twilio_account_sid", credentials[0])
    monkeypatch.setattr(telephony_api.settings, "twilio_auth_token", credentials[1])
    seen: dict[str, Any] = {}

    async def admitted(token: str) -> VoiceSession:
        return VoiceSession(tenant_id=str(uuid.uuid4()), language="de", conversation_id=uuid.uuid4())

    async def pipeline(_transport, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(telephony_api, "open_voice_call", admitted)
    monkeypatch.setattr(telephony_api, "run_studio_session", pipeline)

    with TestClient(_app()).websocket_connect("/telephony/media") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                        "customParameters": {"token": "admitted"},
                    },
                }
            )
        )

    assert callable(seen["transfer"]) is offered
