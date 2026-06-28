"""Conversational workload — the voice/chat surface (voice_agents pattern).

One worked example of a workload, not the headline. The real-time STT->LLM->TTS pipeline
(FastAPI + Pipecat) mounts here; its tool calls (book_appointment, request_refill, ...)
route through the runtime as runbook steps rather than executing directly.

Placeholder — the surface is wired in a later phase.
"""