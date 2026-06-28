"""Wire models for runs — an execution of a runbook against a case."""

from __future__ import annotations

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