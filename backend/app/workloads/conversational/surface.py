"""Pipecat conversational surface shared by Studio and future telephony transports."""

from __future__ import annotations

from pipecat.frames.frames import LLMRunFrame
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
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from app.config import settings
from app.workloads.conversational.sessions import VoiceLanguage

_INSTRUCTIONS = """# Role & Objective
You are Laufwise's German, English, and Arabic conversational test agent. Help the caller describe
an appointment request clearly and naturally. This Studio surface tests conversation quality; it
does not itself change a calendar.

# Personality & Tone
Speak warmly and concisely in the selected session language: German, English, or Arabic. Do not
switch because of an isolated foreign word, name, or brand. If the caller explicitly requests one
of the other supported languages, explain briefly that they can restart the Studio test in that
language. Use one or two short sentences per turn. Do not use markdown. Vary phrasing and never
repeat the same sentence mechanically.

# Tools and Rules
Do not claim to have checked or changed a calendar. A real deployment supplies governed tools for
availability and booking; this generic Studio test has none. Ask for the minimum missing detail.

# Conversation Flow
Greet the caller, learn the requested appointment type and preferred time, then summarize the
request and explain that Studio test mode has not booked it.

# Safety & Escalation
Do not give medical advice. For an emergency, tell the caller to contact emergency services.
"""


def _required(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


async def run_studio_session(transport: BaseTransport, *, language: VoiceLanguage = "de") -> None:
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
            system_instruction=_INSTRUCTIONS,
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

    context = LLMContext()
    user, assistant = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline(
        [transport.input(), stt, user, llm, tts, transport.output(), assistant]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
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
        await runner.cancel()

    await runner.run()
