"""The live voice surface uses the durable executor; a recorder failure cannot allow a write."""

import uuid
from contextlib import asynccontextmanager

import pytest

from app.control_plane.runner import execute_contract
from app.workloads.conversational import booking, recording
from tests.test_voice_booking import _ready


def test_a_voice_booking_uses_its_injected_durable_execution_seam(monkeypatch, tmp_path):
    calls = []
    def durable(contract, provider, tools):
        calls.append(contract.name)
        return execute_contract(contract, {}, tmp_path,
                                real_providers={b.provider: provider for b in contract.state.values()},
                                extra_tools=tools)
    monkeypatch.setattr(booking, "execute_contract", lambda *a, **k: pytest.fail("bypassed durable executor"))
    session = booking.BookingSession("voice-journal", execution=durable)
    _ready(session)
    assert session.book()["status"] == "ok"
    assert calls == ["voice_appointment"]


def test_a_voice_write_without_a_durable_conversation_is_refused(monkeypatch):
    class Session:
        async def get(self, *args):
            return None
    @asynccontextmanager
    async def factory():
        yield Session()
    monkeypatch.setattr(recording, "get_sessionmaker", lambda: factory)
    recorder = recording.ConversationRecorder(uuid.uuid4())
    with pytest.raises(RuntimeError, match="conversation"):
        recorder.execute(None, None, {})
