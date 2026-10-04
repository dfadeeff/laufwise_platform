"""The Studio voice entry point is configured and its signalling tokens fail closed."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import conversational
from app.config import Settings
from app.api.v1.conversational import websocket_url
from app.workloads.conversational import surface
from app.workloads.conversational.sessions import VoiceSession, new_token, token_digest


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


def test_studio_voice_token_is_unguessable_and_only_its_hash_is_kept() -> None:
    token = new_token()

    assert len(token) >= 40
    assert token not in token_digest(token)
    assert token_digest(token) == token_digest(token) != token_digest(new_token())


def test_production_proxy_url_is_returned_as_secure_websocket() -> None:
    assert (
        websocket_url("http://internal:8080/api/v1/conversational/ws", secure=True)
        == "wss://internal:8080/api/v1/conversational/ws"
    )


def test_valid_studio_websocket_completes_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    """The socket hands the pipeline the conversation its turns belong to."""
    conversation_id = uuid.uuid4()
    seen = {}

    async def completed_pipeline(_transport, *, language: str, recorder, calendar=None, config=None, contracts=None, rehearsal=True, base_prompt=None, knowledge=None, test_mode=None) -> None:
        seen["language"] = language
        seen["conversation_id"] = recorder.conversation_id
        return None

    async def admitted(token: str) -> VoiceSession:
        assert token == "admitted"
        return VoiceSession(tenant_id="tenant-a", language="de", conversation_id=conversation_id)

    monkeypatch.setattr(conversational, "run_studio_session", completed_pipeline)
    monkeypatch.setattr(conversational, "open_voice_call", admitted)
    app = FastAPI()
    app.include_router(conversational.router, prefix="/conversational")

    with TestClient(app).websocket_connect("/conversational/ws?token=admitted"):
        pass

    assert seen == {"language": "de", "conversation_id": conversation_id}


def test_rejected_token_closes_with_1008_not_an_opaque_1006(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad token must arrive as a readable close code.

    Closing a WebSocket that was never accepted makes Starlette reject the handshake with
    HTTP 403, which every browser surfaces as an abnormal 1006 with no reason — identical to
    a network failure, and unusable for support. Accepting first costs nothing (no pipeline,
    no provider is reached) and lets the reason through.
    """

    async def unknown(token: str):
        raise KeyError(token)

    monkeypatch.setattr(conversational, "open_voice_call", unknown)
    app = FastAPI()
    app.include_router(conversational.router, prefix="/conversational")

    with TestClient(app).websocket_connect("/conversational/ws?token=expired") as ws:
        message = ws.receive()

    assert message["type"] == "websocket.close"
    assert message["code"] == 1008
    assert "invalid or expired" in message["reason"]


# --- the call ends by itself ------------------------------------------------------------------


def test_silence_escalates_once_then_warns_then_ends() -> None:
    """Three rungs, in order, and only the last one ends the call."""
    said = [surface.idle_instruction(step, "de") for step in range(3)]

    assert [ends for _, ends in said] == [False, False, True]
    assert len({text for text, _ in said}) == 3


def test_a_fourth_silence_repeats_the_goodbye_rather_than_crashing() -> None:
    """The index is clamped: a live call must never raise out of an event handler."""
    assert surface.idle_instruction(9, "de") == surface.idle_instruction(2, "de")


def test_every_rung_exists_in_every_language_the_agent_speaks() -> None:
    """A missing translation would raise mid-call, in the one moment nobody is listening."""
    for language in ("de", "en", "ru", "ar"):
        for step in range(len(surface.IDLE_LADDER)):
            instruction, _ = surface.idle_instruction(step, language)
            assert instruction.strip()
        assert surface.WRAP_UP_INSTRUCTION[language].strip()


def test_the_wrap_up_leaves_room_to_wrap_up() -> None:
    """A wrap-up that fires after the ceiling is a wrap-up nobody hears."""
    assert 0 < surface.WRAP_UP_AFTER_SECONDS < surface.MAX_CALL_SECONDS
    assert surface.MAX_CALL_SECONDS - surface.WRAP_UP_AFTER_SECONDS >= 30
    assert surface.GOODBYE_GRACE_SECONDS < surface.IDLE_SECONDS


def test_the_ladder_ends_with_an_ending() -> None:
    """If the last rung ever stopped ending the call, silence would loop forever."""
    assert surface.IDLE_LADDER[-1] == "end"
    assert surface.idle_instruction(len(surface.IDLE_LADDER) - 1, "en")[1] is True


def test_the_eval_path_is_handed_every_capability_in_a_stable_order() -> None:
    """The suite replays the config-less prompt. If capability selection could reach it, the
    assembled prompt would change, `prompt_sha` would move, and `--compare` against the existing
    76-scenario baseline would stop meaning anything."""
    from app.workloads.conversational.capabilities import resolve
    from app.workloads.conversational.skills import load_skills

    # Every skill a practice could have; a runtime-only one (ADR-0014 D2) never reaches this path.
    catalogue = tuple(skill for skill in load_skills() if skill.default)

    assert resolve().names == tuple(skill.name for skill in catalogue)
    assert list(resolve().names) == sorted(resolve().names)

    prompt = surface._instructions("de")
    for skill in catalogue:
        assert skill.display_name in prompt
    assert "Check availability" not in prompt


# --- speech-to-speech ---------------------------------------------------------------------------


def _realtime_config():
    from app.agents.config import AgentConfig

    return AgentConfig(voice_engine="realtime", voice_id="")


def test_both_engines_are_handed_the_same_instructions() -> None:
    """The engine changes how the call is heard, never what the agent is or may do. If these
    diverge, the eval suite stops proving anything about a realtime call."""
    config = _realtime_config()

    assert surface._instructions("de", config) == surface._instructions(
        "de", config.model_copy(update={"voice_engine": "cascaded"})
    )


def test_realtime_and_cascaded_offer_the_identical_tool_set() -> None:
    from app.workloads.conversational.capabilities import resolve

    config = _realtime_config()

    assert resolve(config).tools == resolve(
        config.model_copy(update={"voice_engine": "cascaded"})
    ).tools


def test_a_realtime_agent_may_not_also_pick_a_synthesis_voice() -> None:
    """It speaks with its own voice, so an ElevenLabs selection would be a control that does
    nothing — the publish gate says so rather than ignoring it."""
    from app.agents.config import AgentConfig

    issues = AgentConfig(voice_engine="realtime", voice_id="some-elevenlabs-voice").publish_issues()

    assert any("realtime agent speaks with its own voice" in issue for issue in issues)


def test_arabic_stays_on_the_engine_that_was_tested_for_it() -> None:
    from app.agents.config import AgentConfig

    issues = AgentConfig(voice_engine="realtime", locale="ar").publish_issues()

    assert any("Arabic runs on the standard voice engine" in issue for issue in issues)


def test_the_eval_report_never_claims_to_have_tested_speech_to_speech() -> None:
    from app.workloads.conversational.evals import runner

    assert runner.snapshot()["transport"] == "cascaded"


def test_the_environment_can_switch_realtime_off_but_never_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kill switch reverts a live realtime agent without a publish; it cannot promote an
    agent whose published contract never asked for speech-to-speech."""
    from app.agents.config import AgentConfig
    from app.config import settings as live_settings

    realtime = AgentConfig(voice_engine="realtime", voice_id="")
    cascaded = AgentConfig(voice_engine="cascaded")

    monkeypatch.setattr(live_settings, "voice_realtime_enabled", True)
    assert surface.uses_realtime(realtime) is True
    assert surface.uses_realtime(cascaded) is False

    monkeypatch.setattr(live_settings, "voice_realtime_enabled", False)
    assert surface.uses_realtime(realtime) is False
    assert surface.uses_realtime(cascaded) is False
    assert surface.uses_realtime(None) is False


# --- a tool call must not stop the worker -----------------------------------------------------


def _slow_tool(during, delay=0.2):
    """A stand-in for thevea: a synchronous call that holds its thread for `delay` seconds."""
    from app.workloads.conversational.booking import ToolSpec

    def call(_session, _arguments):
        during.append(+1)
        time.sleep(delay)
        during.append(-1)
        return {"ok": True}

    return ToolSpec(
        name="search_availability", description="", properties={}, required=(), call=call
    )


def _invoke(handler, results):
    async def _done(result, **_kwargs):
        results.append(result)

    return handler(SimpleNamespace(arguments={}, result_callback=_done))


def test_a_slow_tool_leaves_the_event_loop_free_for_every_other_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One worker carries every concurrent call. If a tool blocks the loop, then for those 20 s
    no call on that worker sends or receives any audio. The loop must keep running while the
    calendar answers."""
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool([]),))
    (schema,) = surface._booking_tools(object())
    results: list = []

    async def main() -> int:
        ticks = 0

        async def other_calls() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(other_calls())
        await _invoke(schema.handler, results)
        ticker.cancel()
        return ticks

    assert asyncio.run(main()) >= 10
    assert results == [{"ok": True}]


def test_two_tool_calls_in_one_turn_still_take_turns_on_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipecat runs a turn's function calls in parallel. A BookingSession's draft is not written
    for two threads, so calls on one session must still run one after the other."""
    during: list[int] = []
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool(during, delay=0.05),))
    (schema,) = surface._booking_tools(object())
    results: list = []

    async def main() -> None:
        await asyncio.gather(_invoke(schema.handler, results), _invoke(schema.handler, results))

    asyncio.run(main())

    assert during == [+1, -1, +1, -1]
    assert len(results) == 2


# --- the keys a call needs are checked before anyone is connected -----------------------------


def _no_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(surface.settings, name, None)
    monkeypatch.setattr(surface.settings, "voice_realtime_enabled", True)


def test_a_cascaded_call_needs_all_three_vendors(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_provider_keys(monkeypatch)

    assert surface.missing_voice_keys(None, "de") == [
        "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
    ]


def test_a_realtime_call_needs_only_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demanding Deepgram and ElevenLabs keys for an agent that never uses them refuses a call
    that would have worked."""
    from app.agents.config import AgentConfig

    _no_provider_keys(monkeypatch)

    assert surface.missing_voice_keys(AgentConfig(voice_engine="realtime"), "de") == [
        "OPENAI_API_KEY"
    ]


def test_an_agent_with_its_own_voice_needs_no_shared_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.config import AgentConfig

    _no_provider_keys(monkeypatch)

    assert "ELEVENLABS_VOICE_ID" not in surface.missing_voice_keys(
        AgentConfig(voice_id="practice-voice"), "de"
    )


def test_a_caller_interrupting_a_tool_does_not_let_the_next_one_overtake_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pipecat cancels an in-flight tool call when the caller talks over the agent. The thread
    behind it cannot be cancelled: a booking already sent to thevea lands anyway. So the call
    keeps its place in the sequence and is still recorded, or the next tool runs on the same
    session at the same time and the transcript misses a booking that happened."""
    during: list[int] = []
    recorded: list[str] = []
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool(during, delay=0.1),))

    class _Recorder:
        async def tool(self, name, *_args, **_kwargs):
            recorded.append(name)

    (schema,) = surface._booking_tools(SimpleNamespace(take_executions=list), _Recorder())
    results: list = []

    async def main() -> None:
        interrupted = asyncio.create_task(_invoke(schema.handler, results))
        await asyncio.sleep(0.02)
        interrupted.cancel()
        await _invoke(schema.handler, results)
        await asyncio.sleep(0.15)

    asyncio.run(main())

    assert during == [+1, -1, +1, -1]
    assert recorded == ["search_availability", "search_availability"]
    assert len(results) == 1


# --- a slow tool is covered by a sentence the model never sees --------------------------------


def test_a_slow_tool_is_covered_by_one_moment_please(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence while thevea answers sounds like a dropped line. The platform speaks for the
    model, so the filler is never the model's own words and never enters its context."""
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool([], delay=0.15),))
    monkeypatch.setattr(surface, "ANNOUNCE_AFTER_SECONDS", 0.05)
    said: list[str] = []

    async def announce() -> None:
        said.append("one moment")

    (schema,) = surface._booking_tools(object(), announce=announce)
    results: list = []
    asyncio.run(_invoke(schema.handler, results))

    assert said == ["one moment"]
    assert results == [{"ok": True}]


def test_a_fast_tool_is_answered_without_a_filler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool([], delay=0.0),))
    monkeypatch.setattr(surface, "ANNOUNCE_AFTER_SECONDS", 0.5)
    said: list[str] = []

    async def announce() -> None:
        said.append("one moment")

    (schema,) = surface._booking_tools(object(), announce=announce)
    asyncio.run(_invoke(schema.handler, []))

    assert said == []


def test_the_filler_exists_in_every_language_the_agent_speaks() -> None:
    for language in ("de", "en", "ru", "ar"):
        assert surface.ANNOUNCEMENTS[language]


# --- the agent can end the call and put the caller through ------------------------------------


_TUESDAY_10_BERLIN = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
_SUNDAY_10_BERLIN = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)


def _call_tools(config=None, *, transfer=None, ended=None, at=_TUESDAY_10_BERLIN):
    async def end_call() -> None:
        if ended is not None:
            ended.append(True)

    return {
        schema.name: schema
        for schema in surface._call_tools(
            config,
            language_of=lambda: "de",
            end_call=end_call,
            transfer=transfer,
            now=lambda: at,
        )
    }


def _result(handler, arguments=None):
    seen: dict = {}

    async def _done(result, *, properties=None):
        seen["result"], seen["properties"] = result, properties

    async def main():
        await handler(SimpleNamespace(arguments=arguments or {}, result_callback=_done))
        await asyncio.sleep(0)

    asyncio.run(main())
    return seen


def test_every_call_can_be_ended_by_the_agent_but_only_a_phone_call_transferred() -> None:
    from app.agents.config import AgentConfig

    async def transfer(_number, _language) -> None: ...

    assert set(_call_tools(AgentConfig())) == {"end_call"}
    assert set(_call_tools(AgentConfig(transfer_number="+4989123456"))) == {"end_call"}
    assert set(_call_tools(AgentConfig(), transfer=transfer)) == {"end_call"}
    assert set(
        _call_tools(AgentConfig(transfer_number="+4989123456"), transfer=transfer)
    ) == {"end_call", "transfer_to_staff"}


def test_end_call_hangs_up_without_asking_the_model_for_another_turn() -> None:
    ended: list[bool] = []

    seen = _result(_call_tools(ended=ended)["end_call"].handler)

    assert ended == [True]
    assert seen["properties"].run_llm is False


def test_a_caller_is_put_through_to_the_number_the_practice_configured() -> None:
    from app.agents.config import AgentConfig

    put_through: list[tuple[str, str]] = []

    async def transfer(number, language) -> None:
        put_through.append((number, language))

    tools = _call_tools(AgentConfig(transfer_number="+4989123456"), transfer=transfer)
    seen = _result(tools["transfer_to_staff"].handler, {"reason": "wants a person"})

    assert put_through == [("+4989123456", "de")]
    assert seen["result"]["transferred"] is True
    assert seen["properties"].run_llm is False


def test_nobody_is_dialled_while_the_practice_is_closed() -> None:
    """A Sunday transfer rings an empty room and then drops the caller. A callback is the
    honest answer, and the tool says so instead of trying."""
    from app.agents.config import AgentConfig

    put_through: list = []

    async def transfer(number, language) -> None:
        put_through.append(number)

    tools = _call_tools(
        AgentConfig(transfer_number="+4989123456"), transfer=transfer, at=_SUNDAY_10_BERLIN
    )
    seen = _result(tools["transfer_to_staff"].handler)

    assert put_through == []
    assert seen["result"]["transferred"] is False
    assert "callback" in " ".join(seen["result"]["agent_notes"])


def test_a_transfer_that_fails_is_reported_so_the_agent_can_take_a_callback() -> None:
    from app.agents.config import AgentConfig

    async def transfer(number, language) -> None:
        raise RuntimeError("twilio said no")

    tools = _call_tools(AgentConfig(transfer_number="+4989123456"), transfer=transfer)
    seen = _result(tools["transfer_to_staff"].handler)

    assert seen["result"]["transferred"] is False
    assert "callback" in " ".join(seen["result"]["agent_notes"])
    assert seen["properties"] is None or seen["properties"].run_llm is not False



def test_every_governed_run_a_tool_makes_is_stored_with_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A voice booking's run existed only as JSONL on the container's disk. Each one the session
    made is handed to the recorder after the tool that made it."""
    monkeypatch.setattr(surface, "TOOLS", (_slow_tool([], delay=0.0),))
    made = ["run-a", "run-b"]
    stored: list = []

    class _Recorder:
        async def tool(self, *_args, **_kwargs) -> None: ...

        async def run(self, execution) -> None:
            stored.append(execution)

    def take() -> list:
        taken, made[:] = list(made), []
        return taken

    (schema,) = surface._booking_tools(SimpleNamespace(take_executions=take), _Recorder())
    asyncio.run(_invoke(schema.handler, []))
    asyncio.run(_invoke(schema.handler, []))

    assert stored == ["run-a", "run-b"]
