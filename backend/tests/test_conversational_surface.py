"""The Studio voice entry point is configured and its signalling tokens fail closed."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import conversational
from app.config import Settings
from app.api.v1.conversational import websocket_url
from app.workloads.conversational.sessions import StudioVoiceSessions


def test_voice_provider_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_STT_MODEL", "test-stt")
    monkeypatch.setenv("VOICE_LLM_MODEL", "test-llm")
    monkeypatch.setenv("VOICE_TTS_MODEL", "test-tts")

    configured = Settings(_env_file=None)

    assert configured.voice_stt_model == "test-stt"
    assert configured.voice_llm_model == "test-llm"
    assert configured.voice_tts_model == "test-tts"


def test_language_voice_falls_back_to_shared_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "shared-voice")

    configured = Settings(_env_file=None)

    assert configured.elevenlabs_voice_for("ar") == "shared-voice"


def test_language_specific_voice_overrides_shared_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "shared-voice")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_AR", "arabic-voice")

    configured = Settings(_env_file=None)

    assert configured.elevenlabs_voice_for("ar") == "arabic-voice"
    assert configured.elevenlabs_voice_for("de") == "shared-voice"


def test_studio_voice_token_is_unguessable_and_tenant_bound() -> None:
    sessions = StudioVoiceSessions()

    token = sessions.create("tenant-a")

    assert len(token) >= 40
    assert sessions.authorize(token).tenant_id == "tenant-a"
    with pytest.raises(KeyError):
        sessions.authorize("not-a-real-token")


def test_studio_voice_session_retains_selected_language() -> None:
    sessions = StudioVoiceSessions()

    token = sessions.create("tenant-a", "ar")

    assert sessions.authorize(token).language == "ar"


def test_production_proxy_url_is_returned_as_secure_websocket() -> None:
    assert (
        websocket_url("http://internal:8080/api/v1/conversational/ws", secure=True)
        == "wss://internal:8080/api/v1/conversational/ws"
    )


def test_valid_studio_websocket_completes_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The socket hands the pipeline the conversation its turns belong to."""
    conversation_id = uuid.uuid4()
    seen = {}

    async def completed_pipeline(_transport, *, language: str, recorder) -> None:
        seen["language"] = language
        seen["conversation_id"] = recorder.conversation_id
        return None

    monkeypatch.setattr(conversational, "run_studio_session", completed_pipeline)
    token = conversational.studio_voice_sessions.create(
        "tenant-a", conversation_id=conversation_id
    )
    app = FastAPI()
    app.include_router(conversational.router, prefix="/conversational")

    with TestClient(app).websocket_connect(f"/conversational/ws?token={token}"):
        pass

    assert seen == {"language": "de", "conversation_id": conversation_id}


def test_rejected_token_closes_with_1008_not_an_opaque_1006() -> None:
    """A bad token must arrive as a readable close code.

    Closing a WebSocket that was never accepted makes Starlette reject the handshake with
    HTTP 403, which every browser surfaces as an abnormal 1006 with no reason — identical to
    a network failure, and unusable for support. Accepting first costs nothing (no pipeline,
    no provider is reached) and lets the reason through.
    """
    app = FastAPI()
    app.include_router(conversational.router, prefix="/conversational")

    with TestClient(app).websocket_connect("/conversational/ws?token=expired") as ws:
        message = ws.receive()

    assert message["type"] == "websocket.close"
    assert message["code"] == 1008
    assert "invalid or expired" in message["reason"]
