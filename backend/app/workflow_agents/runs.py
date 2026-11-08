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

A decision (step 3c) holds the run the same way, with no result: the function says which of the
judgments its decide step names it needs, a person takes them with a reason, and the function
computes again with them in. A branch goes on at the step its value names.

An assessment (ADR-0042) holds the run before anything is computed: a model assesses the case
outside the request (`app.assess`), and `complete_assessment` goes on from the next step with it.
Every function after it receives it as `input["assessment"]`. A case whose input already carries
an assessment (a test case) goes straight on.

Every part of a run executes inside the request (or the sweep) that holds the case's lock, and is
committed with the case in one transaction: no run is ever left half done between steps.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import CaseRun, WorkflowAgent, WorkflowCase
from app.functions.sandbox import FunctionFailed
from app.kernel.drafts import amounts_not_in
from app.kernel.procedures import END, Step, decide_step_of
from app.kernel.workflows import Workflow, value_at

GO_ON = None
WAITING = "waiting"  # a handler holds the run here for facts
DECIDING = "deciding"  # ... or for a person's judgment
ASSESSING = "assessing"  # ... or while a model assesses the case


@dataclasses.dataclass(frozen=True)
class Jump:
    """A handler sends the run on at another step (or `end`)."""

    to: str
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

    @property
    def input(self) -> dict:
        """The case's input, with the assessment once there is one: what every function sees."""
        assessed = next((o["assessment"] for o in self.record.outputs.values() if "assessment" in o), None)
        return self.case.input if assessed is None else {**self.case.input, "assessment": assessed}


def _function(step: Step, run: _Run) -> str | Jump | None:
    computed = run.workflow.compute(run.input, run.settings, step.function)
    if computed.decide:
        decide = decide_step_of(run.workflow.procedure, step.id)
        named = {d["field"] for d in decide.decisions} if decide else set()
        if not set(computed.decide) <= named:
            # A judgment the definition never declared as one is not the platform's to hold for.
            raise FunctionFailed("contract", f"{step.function} asks for a decision its decide step does not name")
        run.record.outputs = {
            **run.record.outputs,
            step.id: {"result": None, "waiting_for": [], "decide": list(computed.decide)},
        }
        run.case.assumptions = [*run.case.assumptions, *computed.assumptions]
        return Jump(decide.id)
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
    problems = run.workflow.check(run.input, run.settings, run.result_of(step.of), step.function)
    if problems:
        run.case.problems = list(problems)
        return "failed"
    return GO_ON


def _letters(step: Step, run: _Run) -> str | None:
    result = run.result_of(step.of)
    drafts = [
        dataclasses.asdict(d)
        for d in run.workflow.drafts(run.input, run.settings, result, step.function)
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


def _decide(step: Step, run: _Run) -> str | None:
    asked = run.record.outputs[step.of].get("decide") or []
    if not asked:
        return GO_ON
    pending = [d for d in step.decisions if d["field"] in asked]
    run.record.outputs = {**run.record.outputs, step.id: {"decide": pending}}
    return DECIDING


def _assess(step: Step, run: _Run) -> str | None:
    given = run.case.input.get("assessment")
    if given is not None:
        # Carried by the input (a test case): what the functions compute with, no model asked.
        run.record.outputs = {**run.record.outputs, step.id: {"assessment": given, "by": "input"}}
        return GO_ON
    run.record.outputs = {**run.record.outputs, step.id: {"pending": True}}
    return ASSESSING


def _branch(step: Step, run: _Run) -> str | Jump:
    value = value_at(run.result_of(step.of), step.on)
    key = value if isinstance(value, str) else json.dumps(value)
    target = dict(step.cases).get(key, step.default)
    if not target:
        # No path for this value: ambiguity is an error, never a guess.
        run.case.problems = [f"Verzweigung {step.id}: kein Weg für den Wert {key}"]
        return "failed"
    return Jump(target)


HANDLERS = {
    "function": _function,
    "check": _check,
    "letters": _letters,
    "wait": _wait,
    "decide": _decide,
    "branch": _branch,
    "assess": _assess,
}


def _clear(case: WorkflowCase) -> None:
    case.result, case.drafts = None, []
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []


def _until(waiting: list[dict]) -> datetime:
    """The end of the earliest due date, in Germany: after it, a person decides what now."""
    due = min(date.fromisoformat(w["due"]) for w in waiting)
    return datetime.combine(due + timedelta(days=1), time(), _GERMANY)


def _advance(run: _Run, start: int) -> None:
    record, case, procedure = run.record, run.case, run.workflow.procedure
    index = {s.id: i for i, s in enumerate(procedure)}
    outcome, position = "ready", start
    while position < len(procedure):
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
        if isinstance(ended, Jump):
            record.trace = [*record.trace, {"step": step.id, "kind": step.kind, "to": ended.to}]
            if ended.to == END:
                record.position = len(procedure)
                break
            position = record.position = index[ended.to]
            continue
        record.trace = [*record.trace, {"step": step.id, "kind": step.kind, "ended": ended}]
        if ended == WAITING:
            # Held at the wait: what stands is shown and can be sent; the rest is awaited.
            waiting = record.outputs[step.id]["for"]
            record.position, record.state, record.expires_at = position, "suspended", _until(waiting)
            case.result, case.drafts = run.result_of(step.of), run.drafts
            case.status = WAITING
            return
        if ended == ASSESSING:
            # Held while a model assesses the case: nothing is computed yet, so nothing is shown.
            record.position, record.state = position, "suspended"
            case.result, case.drafts = None, []
            case.status = ASSESSING
            return
        if ended == DECIDING:
            # Held for a person's judgment: there is no result without it, so nothing is shown.
            record.position, record.state = position, "suspended"
            case.result, case.drafts = None, []
            case.status = DECIDING
            return
        position = record.position = position + 1
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
    """Go on with a held run now that the case's input has the facts it waited for, or the
    judgments it was held for. `workflow` is the version the run started on; `sent` the letters
    already sent for the case. A decision computes again from its function step."""
    if record.state != "suspended":
        raise ValueError(f"run {record.id} is {record.state}, not held at a wait")
    held = workflow.procedure[record.position]
    ids = [s.id for s in workflow.procedure]
    record.state, record.expires_at = "running", None
    record.input_revision = case.input_revision
    record.trace = [*record.trace, {"step": held.id, "kind": held.kind, "resumed": case.input_revision}]
    _clear(case)
    run = _Run(case, workflow, agent.settings or {}, record, sent=list(sent), result_step=held.of)
    if held.kind == "decide":
        start = ids.index(held.of)
    else:
        start = ids.index(held.resume) if held.resume else record.position + 1
    _advance(run, start)


def assessing(record: CaseRun | None) -> str | None:
    """The assess step a held run waits at for its assessment; None for any other run."""
    if record is None or record.state != "suspended" or not record.trace:
        return None
    step = record.trace[-1]["step"]
    return step if (record.outputs.get(step) or {}).get("pending") else None


def complete_assessment(
    case: WorkflowCase, agent: WorkflowAgent, workflow: Workflow, record: CaseRun, assessment: dict, trace: list[dict]
) -> None:
    """Go on with a run held for its assessment, from the step after it, on its version."""
    step = assessing(record)
    if step is None:
        raise ValueError(f"run {record.id} is not held for an assessment")
    record.outputs = {**record.outputs, step: {"assessment": assessment, "by": "model", "tools": trace}}
    record.state = "running"
    record.trace = [*record.trace, {"step": step, "kind": "assess", "assessed": len(trace)}]
    _clear(case)
    _advance(_Run(case, workflow, agent.settings or {}, record), record.position + 1)


def fail_assessment(case: WorkflowCase, record: CaseRun, reason: str) -> None:
    """The assessment could not be made: the run ends failed, and the case says why."""
    step = assessing(record)
    record.trace = [*record.trace, {"step": step, "kind": "assess", "failed": "assessment"}]
    record.state, record.outcome, record.finished_at = "failed", "failed", datetime.now(timezone.utc)
    _clear(case)
    case.status = "failed"
    case.problems = [f"Die Einschätzung ist fehlgeschlagen: {reason}"]


def _held(record: CaseRun | None, key: str) -> list[dict]:
    if record is None or record.state != "suspended":
        return []
    return record.outputs[record.trace[-1]["step"]].get(key, [])


def awaited(record: CaseRun | None) -> list[dict]:
    """The facts a held run waits for; nothing for any other."""
    return _held(record, "for")


def to_decide(record: CaseRun | None) -> list[dict]:
    """The judgments a held run waits for: field, question, answers allowed."""
    return _held(record, "decide")


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
