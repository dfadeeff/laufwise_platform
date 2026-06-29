"""Wire models for runs — an execution of a runbook against a case."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class StepStatus(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"      # precondition failed / approval denied — before any tool ran
    REJECTED = "rejected"    # postcondition failed — outcome not accepted
    PENDING = "pending"


class StepResult(BaseModel):
    step_id: str
    status: StepStatus
    reason: str | None = None
    # The failing predicate, surfaced so the console can explain *why* a step blocked/rejected.
    expr: str | None = None
    blocked_tool: str | None = None


class RunRequest(BaseModel):
    """Start a run of a runbook against a case (the input state fixture)."""

    runbook: str
    case: dict


class RunResult(BaseModel):
    run_id: str
    runbook: str
    steps: list[StepResult]
    trace_path: str | None = None


class RunSummary(BaseModel):
    """Listing view of a persisted run (the /runs console list)."""

    run_id: str
    runbook: str
    version: int
    status: str
    started_at: datetime
    trace_ref: str | None = None


class RunDetail(RunSummary):
    """A run with its ordered step results, reconstructed from the episode log."""

    steps: list[StepResult]