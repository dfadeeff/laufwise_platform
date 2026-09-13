"""Pipecat conversational surface shared by Studio and future telephony transports.

Transport and pipeline wiring only. The agent's instructions are a versioned file (`prompts/
base.md`, not a string in this module), and its booking behaviour lives in `booking.py` — this
file just makes both reachable from a real-time audio session.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    LLMRunFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from app.config import settings
from app.workloads.conversational.booking import TOOLS, BookingSession, ToolSpec
from app.workloads.conversational.notifications import send_call_summary
from app.workloads.conversational.practice import load_practice
from app.workloads.conversational.recording import ConversationRecorder
from app.workloads.conversational.sessions import VoiceLanguage
from app.workloads.conversational.skills import allowed_tools, routing_block, skill_prompts

_PROMPT_PATH = Path(__file__).parent / "prompts" / "base.md"

_LANGUAGE_NAMES = {"de": "German", "en": "English", "ru": "Russian", "ar": "Arabic"}

# The agent speaks first: a phone that connects in silence is a phone the caller hangs up. This
# lives at module scope because the eval runner replays it too — a scenario that starts with the
# caller talking into a session that has never greeted is testing a state no live call is ever in,
# and the agent would spend its first turn greeting instead of answering.
GREETING_INSTRUCTION = {
    "de": "Begrüße die anrufende Person jetzt kurz auf Deutsch.",
    "en": "Greet the caller briefly in English now.",
    "ru": "Поприветствуйте звонящего сейчас коротко по-русски.",
    "ar": "رحّب بالمتصل الآن باختصار باللغة العربية.",
}


def _instructions(language: VoiceLanguage) -> str:
    """The agent's versioned instructions, with the runtime's small declared variable set filled.

    The prompt is English whatever the caller speaks: it tells the agent which language to answer
    in rather than being translated, so one reviewed file governs all four.

    `knowledge` is the practice's own facts — hours, prices, the approved wordings — rendered from
    the configuration file at call time rather than written into the prompt (spec §5). The prompt
    owns the RULES for using those facts; the config owns the facts, and a price change is
    therefore a config edit and not a prompt review.

    `skills` and `skill_prompts` come from `skills/`, so base.md holds only what is true of every
    call and each capability stays a page you can review on its own. The routing list is generated
    from the skill manifests rather than restated here — a renamed skill cannot fall out of sync
    with the prompt that routes to it.
    """
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    practice = load_practice()
    # The TIME, not just the date. A caller says "this afternoon", "in an hour", "später heute";
    # an agent given only a date resolves those against nothing and picks a plausible-looking
    # hour. Observed: "in drei Stunden" became 15:00 on a call that started at 15:56.
    now = datetime.now(ZoneInfo(practice.schedule.timezone))
    variables = {
        "agent_name": "Laufwise",
        "practice_name": practice.name,
        "language_name": _LANGUAGE_NAMES[language],
        "today": now.date().isoformat(),
        "now": f"{now:%A %d %B %Y, %H:%M} ({practice.schedule.timezone})",
        "knowledge": practice.knowledge_block(),
        "skills": routing_block(),
        "skill_prompts": skill_prompts(),
    }
    for name, value in variables.items():
        prompt = prompt.replace(f"{{{{{name}}}}}", value)
    return prompt


class _TranscriptObserver(BaseObserver):
    """Copies the call's speech into the conversation timeline as it is spoken.

    Reads the two frames that carry finished speech: a `TranscriptionFrame` is what the caller
    actually said (STT's final result, not an interim guess), and `TTSTextFrame`s are what the
    agent is sending to be spoken — buffered and flushed when it stops, so a turn is stored as one
    utterance instead of a scatter of clauses.
    """

    def __init__(self, recorder: ConversationRecorder, session: BookingSession) -> None:
        super().__init__()
        self._recorder = recorder
        self._session = session
        self._spoken: list[str] = []

    async def on_push_frame(self, data: FramePushed) -> None:
        frame: Frame = data.frame
        if isinstance(frame, TranscriptionFrame):
            # Counted here because this is the one place a FINISHED caller utterance is observed;
            # it decides whether a call that booked nothing was a question or a false start.
            self._session.caller_turns += 1
            await self._recorder.turn("caller", frame.text)
        elif isinstance(frame, TTSTextFrame):
            self._spoken.append(frame.text)
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._spoken:
            await self._recorder.turn("agent", " ".join(self._spoken))
            self._spoken.clear()


def _booking_tools(
    session: BookingSession, recorder: ConversationRecorder | None = None
) -> list[FunctionSchema]:
    """Bind the shared tool definitions to this call's session, in Pipecat's shape.

    The names, descriptions and parameters come from `booking.TOOLS` rather than being written
    out here, so the eval runner and the live caller reach the same tools described the same way.

    Filtered through the skills' allowlist: a tool no skill claims is not offered to the model at
    all. That is what makes the split a boundary rather than a filing system — the skills own
    their tools, and removing a tool from a skill.json actually removes it from the call.
    """

    def _handler(spec: ToolSpec):
        async def run(params: FunctionCallParams) -> None:
            arguments = dict(params.arguments)
            result = spec.call(session, arguments)
            if recorder is not None:
                await recorder.tool(spec.name, arguments, result)
            await params.result_callback(result)

        return run

    return [
        FunctionSchema(
            name=spec.name,
            description=spec.description,
            properties=spec.properties,
            required=list(spec.required),
            handler=_handler(spec),
        )
        for spec in TOOLS
        if spec.name in allowed_tools()
    ]


def _required(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


# The three languages the practice specification requires the agent to detect and switch between
# mid-call (spec §1). Arabic is not in this set: it runs on its own single-language STT path
# below, so a call that starts in Arabic stays in Arabic.
_SWITCHABLE = (Language.DE, Language.RU, Language.EN)


async def run_studio_session(
    transport: BaseTransport,
    *,
    language: VoiceLanguage = "de",
    recorder: ConversationRecorder | None = None,
    caller_number: str | None = None,
    calendar: object | None = None,
) -> None:
    """Run one real-time session. The transport owns media; this surface owns conversation only."""
    pipecat_language = {
        "de": Language.DE,
        "en": Language.EN,
        "ru": Language.RU,
        "ar": Language.AR,
    }[language]
    if language == "ar":
        stt = DeepgramSTTService(
            api_key=_required(settings.deepgram_api_key, "DEEPGRAM_API_KEY"),
            settings=DeepgramSTTService.Settings(
                model="nova-3-general",
                language=Language.AR,
                numerals=True,
                smart_format=True,
            ),
        )
    else:
        stt = DeepgramFluxSTTService(
            api_key=_required(settings.deepgram_api_key, "DEEPGRAM_API_KEY"),
            settings=DeepgramFluxSTTService.Settings(
                model=settings.voice_stt_model,
                # All three, with the selected one first. The spec requires the agent to follow a
                # caller who changes language mid-call (§1), and a single hint makes the other two
                # arrive as garbled versions of the hinted one — which the agent then answers.
                language_hints=[
                    pipecat_language,
                    *(lang for lang in _SWITCHABLE if lang != pipecat_language),
                ],
                min_confidence=0.3,
                eot_timeout_ms=2500,
            ),
        )
    llm = OpenAILLMService(
        api_key=_required(settings.openai_api_key, "OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            model=settings.voice_llm_model,
            system_instruction=_instructions(language),
            temperature=0.2,
        ),
    )
    # The language pin is a URL field: changing it needs a websocket reconnect, so pinning it
    # would freeze the call in whichever language it started. On the switchable path it is left
    # unset and the multilingual model follows the text the agent produces — which is the only
    # arrangement in which "switch when the caller switches" can actually be honoured. Arabic keeps
    # its pin, because that path never switches.
    tts_settings: dict[str, object] = {
        "voice": _required(settings.elevenlabs_voice_for(language), "ELEVENLABS_VOICE_ID"),
        "model": settings.voice_tts_model,
        "speed": 0.95,
    }
    if language == "ar":
        tts_settings["language"] = pipecat_language
    tts = ElevenLabsTTSService(
        api_key=_required(settings.elevenlabs_api_key, "ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(**tts_settings),  # type: ignore[arg-type]
    )

    # One session per call: its own draft and its own calendar, so two Studio testers never see
    # each other's appointments. Its id IS the conversation id where there is one, so the
    # `call_id` in the summary email and the stored transcript are the same handle — spec §3.9
    # says the call id must be enough to find the call, and two ids would make that a lookup.
    booking = BookingSession(
        recorder.conversation_id.hex if recorder is not None else uuid.uuid4().hex,
        # Resolved by the caller from the instance's bound connection — the practice's real thevea
        # calendar on a deployed number, the in-memory sandbox in the Studio. Passed in rather than
        # constructed here so this module keeps knowing nothing about connections (CLAUDE.md §0).
        calendar=calendar,
    )
    context = LLMContext(tools=_booking_tools(booking, recorder))
    user, assistant = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [transport.input(), stt, user, llm, tts, transport.output(), assistant]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[_TranscriptObserver(recorder, booking)] if recorder else None,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        context.add_message({"role": "developer", "content": GREETING_INSTRUCTION[language]})
        await worker.queue_frames([LLMRunFrame()])

    # "After every accepted call without exception" (spec §3.9) has to survive the ways a call
    # actually ends: a caller hanging up, a transport dropping, a pipeline raising. So the send
    # is guarded by a flag and reached from BOTH the disconnect handler and the finally below —
    # whichever happens first sends it, and the other is a no-op.
    finished = False

    async def _close_out() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        summary = booking.summary()
        delivery = await send_call_summary(
            summary,
            language=language,
            caller_number=caller_number,
            conversation_id=recorder.conversation_id if recorder else None,
        )
        if recorder is not None:
            await recorder.summary(summary, delivery)
            await recorder.finish()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        await _close_out()
        await runner.cancel()

    try:
        await runner.run()
    finally:
        await _close_out()
