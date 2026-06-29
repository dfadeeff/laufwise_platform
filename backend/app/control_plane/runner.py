"""Pure execution core: run a compiled template through LocalEngine. No DB, no I/O beyond the
JSONL trace. Kept separate from `Runtime` so the governance behavior is unit-testable without a
database, and so persistence (Stage 2) wraps — rather than entangles — the engine loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from laufwise.engine.base import StepResult as EngineStepResult
from laufwise.engine.base import StepStatus as EngineStatus

from app.control_plane.factory import build_local_engine
from app.providers.memory import MemoryStateProvider
from app.schemas.run import StepResult, StepStatus
from app.templates.compiler import to_runbook_spec
from app.templates.contract import TemplateContract

_STATUS_MAP = {
    EngineStatus.OK: StepStatus.OK,
    EngineStatus.BLOCK: StepStatus.BLOCKED,
    EngineStatus.REJECT: StepStatus.REJECTED,
}


@dataclass
class ExecResult:
    run_id: str
    runbook: str
    version: int
    status: str  # overall: the worst step status (ok | blocked | rejected)
    steps: list[StepResult]
    trace_path: str


def _map_step(r: EngineStepResult) -> StepResult:
    return StepResult(
        step_id=r.step_id,
        status=_STATUS_MAP[r.status],
        reason=r.reason,
        expr=r.expr,
        blocked_tool=r.blocked_tool,
    )


def _overall(steps: list[StepResult]) -> str:
    for bad in (StepStatus.BLOCKED, StepStatus.REJECTED):
        if any(s.status == bad for s in steps):
            return bad.value
    return StepStatus.OK.value


def execute_contract(contract: TemplateContract, case: dict, runs_dir: str | Path) -> ExecResult:
    """Compile the template, run its enforced steps against the case, return mapped results."""
    fixture = dict(case)
    param_values = fixture.pop("param_values", {})

    spec = to_runbook_spec(contract, param_values)
    provider = MemoryStateProvider(fixture)

    run_id = uuid.uuid4().hex  # full hex so it round-trips to a UUID PK in the DB (Stage 2)
    trace_path = Path(runs_dir) / f"{run_id}.jsonl"
    engine = build_local_engine(provider, trace_path)

    steps = [_map_step(r) for r in engine.run(spec)]
    return ExecResult(
        run_id=run_id,
        runbook=contract.name,
        version=contract.version,
        status=_overall(steps),
        steps=steps,
        trace_path=str(trace_path),
    )