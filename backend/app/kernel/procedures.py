"""A definition's procedure: the steps a case runs through (ADR-0039 D3, step 3).

A step has an id, a kind, and what the kind needs. The kinds the platform runs today:

    function  calls one of the definition's functions on the case's input
    check     the postcondition of a function step (`of`): always right after it
    letters   writes the letters from a checked function step's result (`of`)
    wait      holds the run while a checked function step (`of`) waits for facts: a field of the
              input a named party fills by a date. Once a person enters them, the run goes on
              at `resume` (a function step at or before the wait, so the result is computed
              again with the facts in), or at the next step. Nothing awaited: it goes straight on.

More kinds follow (decide, branch, approval, send); each is a step the platform owns, so a
definition chooses its order, never how one behaves.

The rule the shape enforces: **every function step is followed by its check**. A procedure that
removes or moves the check is not a procedure the platform runs (ADR-0039 D4, "can be switched
off: no"). A definition with no `procedure` runs the default one: compute, its check, then its
letters if it writes any. The default is derived, not stored, so a definition's content (and its
checksum) does not change by having one.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("function", "check", "letters", "wait")


class ProcedureError(ValueError):
    """A procedure the platform cannot run as it stands."""


@dataclass(frozen=True)
class Step:
    id: str
    kind: str
    function: str = ""  # the definition's function this step calls
    of: str = ""  # check, letters, wait: the function step whose result they work on
    resume: str = ""  # wait: the function step the run goes on at once the facts are in


def default_procedure(functions: list[str] | tuple[str, ...]) -> list[dict]:
    steps = [
        {"id": "compute", "kind": "function", "function": "compute"},
        {"id": "check", "kind": "check", "function": "check", "of": "compute"},
    ]
    if "letters" in functions:
        steps.append({"id": "letters", "kind": "letters", "function": "letters", "of": "compute"})
    return steps


def procedure_of(content: dict) -> tuple[Step, ...]:
    """The steps a definition's cases run, checked."""
    # A stated procedure is run as stated, or refused; only one not stated at all is derived.
    raw = content["procedure"] if "procedure" in content else default_procedure(content.get("functions") or [])
    steps = tuple(
        Step(
            id=str(s.get("id", "")),
            kind=str(s.get("kind", "")),
            function=str(s.get("function", "")),
            of=str(s.get("of", "")),
            resume=str(s.get("resume", "")),
        )
        for s in raw
    )
    check_procedure(steps, set(content.get("functions") or []))
    return steps


def check_procedure(steps: tuple[Step, ...], functions: set[str]) -> None:
    ids = [s.id for s in steps]
    if not steps:
        raise ProcedureError("a procedure has at least one step")
    if len(set(ids)) != len(ids) or not all(ids):
        raise ProcedureError("every step has an id of its own")
    checked: set[str] = set()
    function_steps = {s.id for s in steps if s.kind == "function"}
    for i, step in enumerate(steps):
        if step.kind not in KINDS:
            raise ProcedureError(f"step {step.id!r}: the platform has no step kind {step.kind!r}")
        if step.kind == "wait":
            if step.function:
                raise ProcedureError(f"wait step {step.id!r} calls no function")
            if step.resume and step.resume not in function_steps.intersection(ids[: i + 1]):
                raise ProcedureError(f"wait step {step.id!r} resumes at {step.resume!r}, which is no function step before it")
        elif step.function not in functions:
            raise ProcedureError(f"step {step.id!r} calls {step.function!r}, which the definition does not declare")
        if step.kind == "function":
            following = steps[i + 1] if i + 1 < len(steps) else None
            if following is None or following.kind != "check" or following.of != step.id:
                raise ProcedureError(f"function step {step.id!r} is not followed by its check")
        elif step.of not in function_steps:
            raise ProcedureError(f"step {step.id!r} works on {step.of!r}, which is no function step")
        if step.kind == "check":
            checked.add(step.of)
        if step.kind in ("letters", "wait") and step.of not in checked:
            raise ProcedureError(f"{step.kind} step {step.id!r} comes before the check of {step.of!r}")
