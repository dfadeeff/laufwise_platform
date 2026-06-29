"""Assemble a laufwise LocalEngine from the platform's seam implementations.

This is the one place the concrete engine wiring lives, so swapping LocalEngine -> TemporalEngine
later (PLATFORM_PLAN Phase 2) is a change here, not across the app. For v1 the engine is LocalEngine
with a SimulatedAdapter (applies a step's declared `effect` to the in-memory provider so a
postcondition can verify a real state change). Stage 5 replaces the provider/adapter with the real
Google Calendar connector for the `calendar` binding.
"""

from __future__ import annotations

from pathlib import Path

from laufwise.adapters.base import SimulatedAdapter
from laufwise.approval.base import AutoApprovalGate
from laufwise.contract.evaluator import BuiltinEvaluator
from laufwise.engine.local import LocalEngine
from laufwise.state.base import StateProvider
from laufwise.trace.jsonl import JsonlTraceSink


def build_local_engine(provider: StateProvider, trace_path: str | Path) -> LocalEngine:
    """LocalEngine over the given provider, writing an append-only JSONL trace.

    AutoApprovalGate is the v0 stub (Stage 8 replaces it with the DB-backed approval queue).
    SimulatedAdapter enforces the per-step allowlist and applies declared effects for the demo.
    """
    return LocalEngine(
        provider=provider,
        evaluator=BuiltinEvaluator(),
        trace=JsonlTraceSink(trace_path),
        approval=AutoApprovalGate(),
        adapter=SimulatedAdapter(provider),
    )