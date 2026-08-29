"""Pipecat conversational surface shared by Studio and future telephony transports.

Transport and pipeline wiring only. The agent's instructions are a versioned file (`prompts/
base.md`, not a string in this module), and its booking behaviour lives in `booking.py` — this
file just makes both reachable from a real-time audio session.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

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
from app.workloads.conversational.recording import ConversationRecorder
from app.workloads.conversational.sessions import VoiceLanguage

_PROMPT_PATH = Path(__file__).parent / "prompts" / "base.md"

_LANGUAGE_NAMES = {"de": "German", "en": "English", "ar": "Arabic"}


def _instructions(language: VoiceLanguage) -> str:
    """The agent's versioned instructions, with the runtime's small declared variable set filled.

    The prompt is English whatever the caller speaks: it tells the agent which language to answer
    in rather than being translated, so one reviewed file governs all three.
    """
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    variables = {
        "agent_name": "Laufwise",
        "language_name": _LANGUAGE_NAMES[language],
        "today": date.today().isoformat(),
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

    def __init__(self, recorder: ConversationRecorder) -> None:
        super().__init__()
        self._recorder = recorder
        self._spoken: list[str] = []

    async def on_push_frame(self, data: FramePushed) -> None:
        frame: Frame = data.frame
        if isinstance(frame, TranscriptionFrame):
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
    ]


def _required(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


async def run_studio_session(
    transport: BaseTransport,
    *,
    language: VoiceLanguage = "de",
    recorder: ConversationRecorder | None = None,
) -> None:
    """Run one real-time session. The transport owns media; this surface owns conversation only."""
    pipecat_language = {"de": Language.DE, "en": Language.EN, "ar": Language.AR}[language]
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
                language_hints=[pipecat_language],
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
    tts = ElevenLabsTTSService(
        api_key=_required(settings.elevenlabs_api_key, "ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=_required(settings.elevenlabs_voice_for(language), "ELEVENLABS_VOICE_ID"),
            model=settings.voice_tts_model,
            language=pipecat_language,
            speed=0.95,
        ),
    )

    # One booking session per call: its own draft and its own sandbox calendar, so two Studio
    # testers never see each other's appointments.
    booking = BookingSession(uuid.uuid4().hex)
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
        observers=[_TranscriptObserver(recorder)] if recorder else None,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        greeting = {
            "de": "Begrüße die anrufende Person jetzt kurz auf Deutsch.",
            "en": "Greet the caller briefly in English now.",
            "ar": "رحّب بالمتصل الآن باختصار باللغة العربية.",
        }
        context.add_message({"role": "developer", "content": greeting[language]})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        if recorder is not None:
            await recorder.finish()
        await runner.cancel()

    await runner.run()
