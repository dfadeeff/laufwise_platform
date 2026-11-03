"""Running a case through its definition's procedure (ADR-0039 D3, step 3): no FastAPI.

A run walks the procedure step by step. Each step kind has one handler here, and a handler either
lets the run go on, holds it, or ends it with an outcome; a kind added later (decide, branch) is a
new handler, not a change to the loop. The run, its position and every step's output are stored,
so a held run resumes from where it stopped, in a later request, on the version it started on.

The rules are the ones a case always had, now per step: a function's result goes no further than
its check; a letter may say no amount the result does not hold; a function that fails, by a limit
or by answering in the wrong shape, ends the run as failed and nothing it produced is kept.

A wait (step 3b) holds the run while facts are missing: the case shows what stands, its letters
can be approved and sent, and it names what is awaited, from whom and by when. A person entering
the facts resumes it; the date passing ends it as expired, and the case asks a person what now.
A letter identical to one already sent for the case is not offered again when the run resumes.

Every part of a run executes inside the request (or the sweep) that holds the case's lock, and is
committed with the case in one transaction: no run is ever left half done between steps.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import CaseRun, WorkflowAgent, WorkflowCase
from app.functions.sandbox import FunctionFailed
from app.kernel.drafts import amounts_not_in
from app.kernel.procedures import Step
from app.kernel.workflows import Workflow

GO_ON = None
WAITING = "waiting"  # a handler holds the run here
_GERMANY = ZoneInfo("Europe/Berlin")


@dataclasses.dataclass
class _Run:
    """What a run's steps share while it runs."""

    case: WorkflowCase
    workflow: Workflow
    settings: dict
    record: CaseRun
    drafts: list[dict] = dataclasses.field(default_factory=list)
    sent: list[dict] = dataclasses.field(default_factory=list)  # letters already sent for the case
    result_step: str = ""  # the function step whose result the case shows

    def result_of(self, step_id: str) -> dict:
        return self.record.outputs[step_id]["result"]


def _function(step: Step, run: _Run) -> str | None:
    computed = run.workflow.compute(run.case.input, run.settings, step.function)
    if computed.waiting_for and not any(
        s.kind == "wait" and s.of == step.id for s in run.workflow.procedure
    ):
        # Shown as ready, it would look settled while it is not.
        raise FunctionFailed("contract", f"{step.function} waits for facts, and no wait step holds the run for them")
    run.record.outputs = {
        **run.record.outputs,
        step.id: {"result": computed.result, "waiting_for": list(computed.waiting_for)},
    }
    run.result_step = step.id
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
    # Already in the tenant's hands, exactly so: sending it again would say it twice.
    run.drafts.extend(d for d in drafts if d not in run.sent)
    return GO_ON


def _wait(step: Step, run: _Run) -> str | None:
    waiting = run.record.outputs[step.of]["waiting_for"]
    if not waiting:
        return GO_ON
    run.record.outputs = {**run.record.outputs, step.id: {"for": waiting}}
    return WAITING


HANDLERS = {"function": _function, "check": _check, "letters": _letters, "wait": _wait}


def _clear(case: WorkflowCase) -> None:
    case.result, case.drafts = None, []
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []


def _until(waiting: list[dict]) -> datetime:
    """The end of the earliest due date, in Germany: after it, a person decides what now."""
    due = min(date.fromisoformat(w["due"]) for w in waiting)
    return datetime.combine(due + timedelta(days=1), time(), _GERMANY)


def _advance(run: _Run, start: int) -> None:
    record, case, procedure = run.record, run.case, run.workflow.procedure
    outcome = "ready"
    for position in range(start, len(procedure)):
        step = procedure[position]
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
        if ended == WAITING:
            # Held at the wait: what stands is shown and can be sent; the rest is awaited.
            waiting = record.outputs[step.id]["for"]
            record.position, record.state, record.expires_at = position, "suspended", _until(waiting)
            case.result, case.drafts = run.result_of(step.of), run.drafts
            case.status = WAITING
            return
        record.position = position + 1
        if ended is not GO_ON:
            outcome = ended
            break
    if record.state == "running":
        record.state = "succeeded"
    if outcome == "ready":
        case.result, case.drafts = run.result_of(run.result_step), run.drafts
    record.outcome = outcome
    record.finished_at = datetime.now(timezone.utc)
    case.status = outcome


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
    _clear(case)
    _advance(_Run(case, workflow, agent.settings or {}, record), 0)
    return record


def resume_run(
    case: WorkflowCase,
    agent: WorkflowAgent,
    workflow: Workflow,
    record: CaseRun,
    sent: list[dict],
) -> None:
    """Go on with a held run now that the case's input has the facts it waited for. `workflow` is
    the version the run started on; `sent` the letters already sent for the case."""
    if record.state != "suspended":
        raise ValueError(f"run {record.id} is {record.state}, not held at a wait")
    wait = workflow.procedure[record.position]
    ids = [s.id for s in workflow.procedure]
    record.state, record.expires_at = "running", None
    record.input_revision = case.input_revision
    record.trace = [*record.trace, {"step": wait.id, "kind": "wait", "resumed": case.input_revision}]
    _clear(case)
    run = _Run(case, workflow, agent.settings or {}, record, sent=list(sent), result_step=wait.of)
    _advance(run, ids.index(wait.resume) if wait.resume else record.position + 1)


def awaited(record: CaseRun | None) -> list[dict]:
    """What a held run waits for; nothing for any other."""
    if record is None or record.state != "suspended":
        return []
    return record.outputs[record.trace[-1]["step"]]["for"]


def expire(case: WorkflowCase, record: CaseRun) -> None:
    """The date passed without the facts: the run ends, and a person decides what now."""
    waiting = awaited(record)
    record.state, record.outcome = "expired", "blocked"
    record.expires_at, record.finished_at = None, datetime.now(timezone.utc)
    _clear(case)
    case.status = "blocked"
    case.questions = [
        f"{w['what']}: vom {w['from']} bis {date.fromisoformat(w['due']):%d.%m.%Y} erwartet und "
        "nicht eingegangen. Tragen Sie es ein, sobald es vorliegt, und berechnen Sie neu, oder "
        "entscheiden Sie anders."
        for w in waiting
    ]


def cancel(record: CaseRun | None) -> None:
    """The case changed under a held run: it no longer describes the case, and ends."""
    if record is not None and record.state == "suspended":
        record.state, record.outcome = "cancelled", None
        record.expires_at, record.finished_at = None, datetime.now(timezone.utc)
