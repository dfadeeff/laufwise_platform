"""Conversational workload — the real-time voice/chat surface (agent taxonomy, tier 3).

The STT->LLM->TTS pipeline (FastAPI + Pipecat) mounts in `surface.py`; its instructions are a
versioned file in `prompts/`. Consequential tool calls do not execute directly — `booking.py`
routes them through the runtime as governed runbook steps, because a real-time surface cannot
wait for a reviewer and so must not be its own governance authority.
"""
