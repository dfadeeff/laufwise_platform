"""Running a case through its definition's procedure (ADR-0039 D3, step 3): no FastAPI.

A run walks the procedure step by step. Each step kind has one handler here, and a handler either
lets the run go on or ends it with an outcome; a kind added later (wait, decide, branch) is a new
handler, not a change to the loop. The run, its position and every step's output are stored, so
a run that pauses (from step 3b) resumes from where it stopped.

The rules are the ones a case always had, now per step: a function's result goes no further than
its check; a letter may say no amount the result does not hold; a function that fails, by a limit
or by answering in the wrong shape, ends the run as failed and nothing it produced is kept.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone

from app.db.models import CaseRun, WorkflowAgent, WorkflowCase
from app.functions.sandbox import FunctionFailed
from app.kernel.drafts import amounts_not_in
from app.kernel.procedures import Step
from app.kernel.workflows import Workflow

GO_ON = None


@dataclasses.dataclass
class _Run:
    """What a run's steps share while it runs."""

    case: WorkflowCase
    workflow: Workflow
    settings: dict
    record: CaseRun
    drafts: list[dict] = dataclasses.field(default_factory=list)

    def result_of(self, step_id: str) -> dict:
        return self.record.outputs[step_id]["result"]


def _function(step: Step, run: _Run) -> str | None:
    computed = run.workflow.compute(run.case.input, run.settings, step.function)
    run.record.outputs = {**run.record.outputs, step.id: {"result": computed.result}}
    run.case.assumptions = [*run.case.assumptions, *computed.assumptions]
    if computed.errors:
        run.case.errors = list(computed.errors)
        return "draft"
    if computed.questions:
        run.case.questions = list(computed.questions)
        return "blocked"
    return GO_ON


def _check(step: Step, run: _Run) -> str | None:
    # The postcondition, called by the platform in its own sandbox call: a definition cannot
    # skip it (the procedure's shape forbids that), and a result it refuses is never shown.
    problems = run.workflow.check(run.case.input, run.settings, run.result_of(step.of), step.function)
    if problems:
        run.case.problems = list(problems)
        return "failed"
    return GO_ON


def _letters(step: Step, run: _Run) -> str | None:
    result = run.result_of(step.of)
    drafts = [
        dataclasses.asdict(d)
        for d in run.workflow.drafts(run.case.input, run.settings, result, step.function)
    ]
    # An amount the result does not hold is a failure, and then neither the result nor any
    # letter is shown.
    problems = [
        f"Schreiben an {d['recipient']}: {amount} steht nicht im Ergebnis"
        for d in drafts
        for amount in amounts_not_in(d["text"] + "\n" + d["enclosure"], result)
    ]
    if problems:
        run.case.problems = problems
        return "failed"
    run.drafts.extend(drafts)
    return GO_ON


HANDLERS = {"function": _function, "check": _check, "letters": _letters}


def run_case(case: WorkflowCase, agent: WorkflowAgent, workflow: Workflow) -> CaseRun:
    """Run `case` through the procedure from the start and leave the case showing what it came to.
    The caller adds the returned run to its session and commits both together."""
    record = CaseRun(
        id=uuid.uuid4(),
        tenant_id=case.tenant_id,
        case_id=case.id,
        definition_version_id=uuid.UUID(workflow.version_id) if workflow.version_id else None,
        input_revision=case.input_revision,
        state="running",
        position=0,
        outputs={},
        trace=[],
    )
    case.result, case.drafts = None, []
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []
    run = _Run(case, workflow, agent.settings or {}, record)

    outcome = "ready"
    for position, step in enumerate(workflow.procedure):
        try:
            ended = HANDLERS[step.kind](step, run)
        except FunctionFailed as exc:
            # A function that ran out of a limit, raised, or answered in the wrong shape: nothing
            # it produced is kept, and the case says which limit or error it was.
            record.trace = [*record.trace, {"step": step.id, "kind": step.kind, "failed": exc.kind}]
            record.position, record.state, outcome = position, "failed", "failed"
            case.result, case.drafts = None, []
            case.problems = [f"Die Berechnung ist fehlgeschlagen ({exc.kind}): {exc.message}"]
            break
        record.trace = [*record.trace, {"step": step.id, "kind": step.kind, "ended": ended}]
        record.position = position + 1
        if ended is not GO_ON:
            outcome = ended
            break
    if record.state == "running":
        record.state = "succeeded"
    if outcome == "ready":
        last = [s for s in workflow.procedure if s.kind == "function"][-1]
        case.result, case.drafts = run.result_of(last.id), run.drafts
    record.outcome = outcome
    record.finished_at = datetime.now(timezone.utc)
    case.status = outcome
    return record
