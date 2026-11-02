"""A workflow a module offers, run case by case (ADR-0030, ADR-0039).

A workflow is a definition read from files (`definitions/<module>/<key>/`): its forms, its rules,
its reading guide and its functions. The kernel never imports a definition's code. It runs the
functions in the sandbox (`app.functions`), each step in its own call, and owns everything
around them: storage, revisions, the order they run in, that the check always runs, and that a
letter says nothing the result does not.

    compute(input, settings)         -> Computed    what to correct, or a result
    check(input, settings, result)   -> [problems]  the postcondition, run on every result
    drafts(input, settings, result)  -> [Draft]     the letters, from a checked result

A person changes the input, never the result: there is no way to write a result except by
computing it from an input, and a result that fails its check is never shown as one. What a
function returns is checked for its shape here, because it is code the platform did not write.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping

from app.functions.sandbox import FunctionFailed, Limits, sandbox


@dataclass(frozen=True)
class Computed:
    errors: tuple[str, ...] = ()  # the input cannot be computed; what to correct
    result: dict | None = None  # None when errors or questions block it
    questions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    """A text the workflow writes from a result, e.g. a letter to one tenant (ADR-0034)."""

    title: str
    recipient: str
    text: str
    # A document sent with it, e.g. the itemised statement the letter says is enclosed. Checked
    # against the result exactly like the letter.
    enclosure: str = ""
    enclosure_name: str = ""
    # The letterhead: who writes, and from where (ADR-0037).
    sender: str = ""
    sender_address: str = ""


_DRAFT_FIELDS = {f.name for f in fields(Draft)}


@dataclass(frozen=True)
class Workflow:
    key: str
    label: str
    summary: str
    version: int
    forms: Mapping[str, dict]  # input, settings, result: JSON Schema
    rules: Any  # the rules pack in its JSON form, handed to every function
    # The rules the workflow computes with; recorded with every computation, so a result can be
    # traced to the rules that produced it.
    rules_version: str
    # How to read this workflow's documents into its input. Data, shown to the reading model,
    # never executed.
    extraction_guide: str
    bundle: Path  # what the functions see in the sandbox
    functions: tuple[str, ...]  # compute and check always; letters when it writes any
    limits: Limits = field(default_factory=Limits)

    def schemas(self) -> dict:
        return copy.deepcopy(dict(self.forms))

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "summary": self.summary}

    @property
    def writes_letters(self) -> bool:
        return "letters" in self.functions

    def _call(self, name: str, input: Any, settings: dict) -> Any:
        return sandbox().run(self.bundle, name, input, settings, self.rules, self.limits)

    def compute(self, input: dict, settings: dict) -> Computed:
        out = self._call("compute", input, settings)
        if not isinstance(out, dict):
            raise FunctionFailed("contract", "compute returned no object")
        if "errors" in out:
            return Computed(errors=_texts(out["errors"], "compute.errors"))
        result = out.get("result")
        questions = _texts(out.get("questions", []), "compute.questions")
        if not (isinstance(result, dict) or (result is None and questions)):
            raise FunctionFailed("contract", "compute returned neither a result nor a question")
        return Computed(
            result=None if questions else result,
            questions=questions,
            assumptions=_texts(out.get("assumptions", []), "compute.assumptions"),
        )

    def check(self, input: dict, settings: dict, result: dict) -> list[str]:
        out = self._call("check", {"case": input, "result": result}, settings)
        return list(_texts(out, "check"))

    def drafts(self, input: dict, settings: dict, result: dict) -> list[Draft]:
        if not self.writes_letters:
            return []
        out = self._call("letters", {"case": input, "result": result}, settings)
        if not isinstance(out, list):
            raise FunctionFailed("contract", "letters returned no list")
        drafts = []
        for item in out:
            if (
                not isinstance(item, dict)
                or not set(item) <= _DRAFT_FIELDS
                or not {"title", "recipient", "text"} <= set(item)
                or not all(isinstance(v, str) for v in item.values())
            ):
                raise FunctionFailed("contract", "letters returned a letter that is not a letter")
            drafts.append(Draft(**item))
        return drafts


def _texts(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise FunctionFailed("contract", f"{what} is not a list of texts")
    return tuple(value)
