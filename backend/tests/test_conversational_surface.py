"""The Studio voice entry point is configured and its signalling tokens fail closed."""

from __future__ import annotations

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


def test_studio_voice_token_is_unguessable_and_tenant_bound() -> None:
    sessions = StudioVoiceSessions()

    token = sessions.create("tenant-a")

    assert len(token) >= 40
    assert sessions.authorize(token).tenant_id == "tenant-a"
    with pytest.raises(KeyError):
        sessions.authorize("not-a-real-token")


def test_production_proxy_url_is_returned_as_secure_websocket() -> None:
    assert (
        websocket_url("http://internal:8080/api/v1/conversational/ws", secure=True)
        == "wss://internal:8080/api/v1/conversational/ws"
    )


def test_valid_studio_websocket_completes_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    async def completed_pipeline(_transport) -> None:
        return None

    monkeypatch.setattr(conversational, "run_studio_session", completed_pipeline)
    token = conversational.studio_voice_sessions.create("tenant-a")
    app = FastAPI()
    app.include_router(conversational.router, prefix="/conversational")

    with TestClient(app).websocket_connect(f"/conversational/ws?token={token}"):
        pass
