"""A definition's procedure: the steps a case runs through (ADR-0039 D3, step 3).

A step has an id, a kind, and what the kind needs. The kinds the platform runs:

    function  calls one of the definition's functions on the case's input
    check     the postcondition of a function step (`of`): always right after it
    letters   writes the letters from a checked function step's result (`of`)
    wait      holds the run while a checked function step (`of`) waits for facts: a field of the
              input a named party fills by a date. Once a person enters them, the run goes on
              at `resume` (a function step at or before the wait, so the result is computed
              again with the facts in), or at the next step. Nothing awaited: it goes straight on.
    decide    the fields of the input that are a person's judgment (`decisions`: each a field,
              the question and the answers allowed). When its function step (`of`) asks for
              one, the function gives no result, and the run is held here until a person
              decides, with a reason; then the function computes again with the judgment in.
              Nothing asked: it goes straight on.
    assess    a model assesses the case before anything is computed (ADR-0042 D2): it reads the
              input, the definition's law texts and documents, may call the definition's
              `tools` it names (calculators, in the sandbox), and returns an assessment in the
              definition's `assessment` form, every judgment with its source quoted. The run is
              held while it works; the functions after it receive it as `input["assessment"]`.
              At most one, before every function step. A case whose input carries an
              assessment (a test case) goes straight on.
    branch    goes on at the step `cases` names for a value in a checked function step's
              result (`of`, at the path `on`), or at `default`; a value with no path fails the
              run. Only forward, and `end` ends the run. Never on text a model wrote.

More kinds follow (approval, send); each is a step the platform owns, so a definition chooses
its order, never how one behaves.

The rules the shape enforces: **every function step is followed by its check**, and a step that
works on a function step's result (letters, wait, decide, branch) is reached only once that
result was checked, on every path a branch can take. A procedure that removes, moves or jumps
past a check is not a procedure the platform runs (ADR-0039 D4, "can be switched off: no"). A
definition with no `procedure` runs the default one: compute, its check, then its letters if it
writes any. The default is derived, not stored, so a definition's content (and its checksum)
does not change by having one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("function", "check", "letters", "wait", "decide", "branch", "assess")
END = "end"  # a branch target: the run ends with what it has
# Kinds that work on a checked function step's result.
_ON_RESULT = ("letters", "wait", "decide", "branch")


class ProcedureError(ValueError):
    """A procedure the platform cannot run as it stands."""


@dataclass(frozen=True)
class Step:
    id: str
    kind: str
    function: str = ""  # the definition's function this step calls
    of: str = ""  # check, letters, wait, decide, branch: the function step they work on
    resume: str = ""  # wait: the function step the run goes on at once the facts are in
    # decide: ({field, question, options: [{value, label}]}, ...)
    decisions: tuple = field(default=())
    on: str = ""  # branch: the path in the result it reads
    cases: tuple = field(default=())  # branch: ((value, step id), ...)
    default: str = ""  # branch: where an unlisted value goes; none, and it fails the run
    tools: tuple = field(default=())  # assess: the definition's tools the model may call

    def targets(self) -> tuple[str, ...]:
        return tuple(t for _, t in self.cases) + ((self.default,) if self.default else ())


def default_procedure(functions: list[str] | tuple[str, ...]) -> list[dict]:
    steps = [
        {"id": "compute", "kind": "function", "function": "compute"},
        {"id": "check", "kind": "check", "function": "check", "of": "compute"},
    ]
    if "letters" in functions:
        steps.append({"id": "letters", "kind": "letters", "function": "letters", "of": "compute"})
    return steps


def _step(s: dict) -> Step:
    cases = s.get("cases") or {}
    if not isinstance(cases, dict):
        raise ProcedureError(f"step {s.get('id')!r}: cases map a value to a step")
    return Step(
        id=str(s.get("id", "")),
        kind=str(s.get("kind", "")),
        function=str(s.get("function", "")),
        of=str(s.get("of", "")),
        resume=str(s.get("resume", "")),
        decisions=tuple(s.get("decisions") or ()),
        on=str(s.get("on", "")),
        cases=tuple((str(k), str(v)) for k, v in cases.items()),
        default=str(s.get("default", "")),
        tools=tuple(str(t) for t in s.get("tools") or ()),
    )


def procedure_of(content: dict) -> tuple[Step, ...]:
    """The steps a definition's cases run, checked."""
    # A stated procedure is run as stated, or refused; only one not stated at all is derived.
    raw = content["procedure"] if "procedure" in content else default_procedure(content.get("functions") or [])
    steps = tuple(_step(s) for s in raw)
    tools = {t.get("name") for t in content.get("tools") or [] if isinstance(t, dict)}
    check_procedure(steps, set(content.get("functions") or []), tools)
    return steps


def decide_step_of(steps: tuple[Step, ...], function_step: str) -> Step | None:
    return next((s for s in steps if s.kind == "decide" and s.of == function_step), None)


def judgment_fields(steps: tuple[Step, ...]) -> set[str]:
    """The input fields only a decision sets: a person's judgment, with a reason."""
    return {d["field"] for s in steps if s.kind == "decide" for d in s.decisions}


def _check_decisions(step: Step) -> None:
    if not step.decisions:
        raise ProcedureError(f"decide step {step.id!r} names the decisions it takes")
    for d in step.decisions:
        options = d.get("options") if isinstance(d, dict) else None
        if (
            not isinstance(d, dict)
            or not all(isinstance(d.get(k), str) and d[k].strip() for k in ("field", "question"))
            or not isinstance(options, list)
            or len(options) < 2
            or not all(isinstance(o, dict) and isinstance(o.get("value"), str) and o.get("label") for o in options)
        ):
            raise ProcedureError(
                f"decide step {step.id!r}: each decision names a field, a question and at least two answers"
            )


def check_procedure(steps: tuple[Step, ...], functions: set[str], tools: set[str] = frozenset()) -> None:
    ids = [s.id for s in steps]
    if not steps:
        raise ProcedureError("a procedure has at least one step")
    if len(set(ids)) != len(ids) or not all(ids) or END in ids:
        raise ProcedureError("every step has an id of its own")
    index = {s.id: i for i, s in enumerate(steps)}
    function_steps = {s.id for s in steps if s.kind == "function"}
    for i, step in enumerate(steps):
        if step.kind not in KINDS:
            raise ProcedureError(f"step {step.id!r}: the platform has no step kind {step.kind!r}")
        if step.kind in ("function", "check", "letters"):
            if step.function not in functions:
                raise ProcedureError(f"step {step.id!r} calls {step.function!r}, which the definition does not declare")
        elif step.function:
            raise ProcedureError(f"{step.kind} step {step.id!r} calls no function")
        if step.kind == "assess":
            if step.of:
                raise ProcedureError(f"assess step {step.id!r} works on the case, not on a function step")
            if unknown := set(step.tools) - tools:
                raise ProcedureError(f"assess step {step.id!r} names tools the definition does not declare: {sorted(unknown)}")
            if sum(1 for s in steps if s.kind == "assess") > 1:
                raise ProcedureError("a procedure assesses a case once")
            if any(s.kind == "function" for s in steps[:i]):
                raise ProcedureError(f"assess step {step.id!r} comes after a function step; every function receives the assessment")
            continue
        if step.kind == "function":
            following = steps[i + 1] if i + 1 < len(steps) else None
            if following is None or following.kind != "check" or following.of != step.id:
                raise ProcedureError(f"function step {step.id!r} is not followed by its check")
        elif step.of not in function_steps:
            raise ProcedureError(f"step {step.id!r} works on {step.of!r}, which is no function step")
        if step.kind == "wait" and step.resume and step.resume not in function_steps.intersection(ids[: i + 1]):
            raise ProcedureError(f"wait step {step.id!r} resumes at {step.resume!r}, which is no function step before it")
        if step.kind == "decide":
            _check_decisions(step)
            if sum(1 for s in steps if s.kind == "decide" and s.of == step.of) > 1:
                raise ProcedureError(f"function step {step.of!r} has more than one decide step")
        if step.kind == "branch":
            if not step.on or not step.cases or any(not v for v, _ in step.cases):
                raise ProcedureError(f"branch step {step.id!r} names the value it reads and its paths")
            for target in step.targets():
                if target == END:
                    continue
                if target not in index or index[target] <= i:
                    raise ProcedureError(f"branch step {step.id!r} goes to {target!r}, which is no step after it")
                if steps[index[target]].kind == "check":
                    raise ProcedureError(f"branch step {step.id!r} goes to the check {target!r}, past its function")
    _check_paths(steps, index)


def _check_paths(steps: tuple[Step, ...], index: dict[str, int]) -> None:
    """Every step a run can reach, reached only with the results it works on checked. Jumps go
    forward only (a wait's resume and a decision compute again from a function, and the check
    follows it), so one pass in order sees every path."""
    reaching: list[set[str] | None] = [None] * len(steps)  # checked on every path to the step
    reaching[0] = set()
    for i, step in enumerate(steps):
        checked = reaching[i]
        if checked is None:
            raise ProcedureError(f"no path reaches step {step.id!r}")
        if step.kind in _ON_RESULT and step.of not in checked:
            raise ProcedureError(f"{step.kind} step {step.id!r} comes before the check of {step.of!r}")
        after = checked | {step.of} if step.kind == "check" else checked
        nexts = step.targets() if step.kind == "branch" else ((steps[i + 1].id,) if i + 1 < len(steps) else ())
        for target in nexts:
            if target == END:
                continue
            j, before = index[target], reaching[index[target]]
            reaching[j] = set(after) if before is None else before & after
