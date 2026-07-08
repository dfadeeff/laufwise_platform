"""Assemble a laufwise LocalEngine from the platform's seam implementations.

This is the one place the concrete engine wiring lives, so swapping LocalEngine -> TemporalEngine
later (PLATFORM_PLAN Phase 2) is a change here, not across the app. For v1 the engine is LocalEngine
with a ToolRegistryAdapter: tool implementations write to the provider-backed store through their
own code path — the runbook's declared outcome is never copied into state, so the postcondition
verifies what the tool actually did (non-circular; PLATFORM_PLAN §9). Stage 5 replaces the demo
implementations with the real Google Calendar connector for the `calendar` binding.
"""

from __future__ import annotations

from pathlib import Path

from laufwise.adapters.base import ToolRegistryAdapter
from laufwise.approval.base import AutoApprovalGate
from laufwise.contract.evaluator import BuiltinEvaluator
from laufwise.engine.local import LocalEngine
from laufwise.state.base import StateProvider
from laufwise.trace.jsonl import JsonlTraceSink

from app.workloads.demo_tools import DEMO_TOOLS


def build_local_engine(
    provider: StateProvider,
    trace_path: str | Path,
    extra_tools: dict | None = None,
) -> LocalEngine:
    """LocalEngine over the given provider, writing an append-only JSONL trace.

    AutoApprovalGate is the v0 stub (Stage 8 replaces it with the DB-backed approval queue);
    the engine itself now blocks on a denial, so a real gate drops in without engine changes.
    The engine also asserts the per-step allowlist; the adapter re-checks it (defense in depth).

    `extra_tools` are a resolved connection's tools (e.g. the thevea `book_appointment`); they
    override the demo tools of the same name, so a thevea-bound run writes to the real calendar
    while a simulated run keeps the demo implementation.
    """
    tools = {**DEMO_TOOLS, **(extra_tools or {})}
    return LocalEngine(
        provider=provider,
        evaluator=BuiltinEvaluator(),
        trace=JsonlTraceSink(trace_path),
        approval=AutoApprovalGate(),
        adapter=ToolRegistryAdapter(provider, tools),
    )