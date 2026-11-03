"""A workflow a module offers, run case by case (ADR-0030, ADR-0039).

A workflow is a definition read from files (`definitions/<module>/<key>/`): its forms, its rules,
its reading guide and its functions. The kernel never imports a definition's code. It runs the
functions in the sandbox (`app.functions`), each step in its own call, and owns everything
around them: storage, revisions, the order they run in, that the check always runs, and that a
letter says nothing the result does not.

    compute(input, settings)         -> Computed    what to correct, or a result (and the facts
                                                    it still waits for, from whom, by when; or
                                                    the judgments a person must make first)
    check(input, settings, result)   -> [problems]  the postcondition, run on every result
    drafts(input, settings, result)  -> [Draft]     the letters, from a checked result

A person changes the input, never the result: there is no way to write a result except by
computing it from an input, and a result that fails its check is never shown as one. What a
function returns is checked for its shape here, because it is code the platform did not write.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from app.functions.sandbox import FunctionFailed, Limits, sandbox


@dataclass(frozen=True)
class Computed:
    errors: tuple[str, ...] = ()  # the input cannot be computed; what to correct
    result: dict | None = None  # None when errors or questions block it
    questions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    # Facts the result does not settle yet: each one a field of the input a named party fills by a
    # date (ADR-0039 D3, the wait step). The rest of the result stands.
    waiting_for: tuple[dict, ...] = ()
    # Input fields that are a person's judgment and must be decided before there is a result
    # (ADR-0039 D3, the decide step). Asking for one means giving no result.
    decide: tuple[str, ...] = ()


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
    # The steps a case runs (`app.kernel.procedures`); derived when the definition states none.
    procedure: tuple = ()
    limits: Limits = field(default_factory=Limits)
    # The stored version this workflow runs (ADR-0039 step 2b); a case records it. None when it
    # was read straight from files, as a definition's own tests do.
    version_id: str | None = None
    # What the publish gate reported for this version (ADR-0039 step 4); None when read from files.
    gate: dict | None = None

    def schemas(self) -> dict:
        return copy.deepcopy(dict(self.forms))

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "summary": self.summary}

    @property
    def writes_letters(self) -> bool:
        return "letters" in self.functions

    def _call(self, name: str, input: Any, settings: dict) -> Any:
        return sandbox().run(self.bundle, name, input, settings, self.rules, self.limits)

    def compute(self, input: dict, settings: dict, function: str = "compute") -> Computed:
        out = self._call(function, input, settings)
        if not isinstance(out, dict):
            raise FunctionFailed("contract", "compute returned no object")
        if "errors" in out:
            return Computed(errors=_texts(out["errors"], "compute.errors"))
        result = out.get("result")
        questions = _texts(out.get("questions", []), "compute.questions")
        decide = () if questions else _texts(out.get("decide", []), "compute.decide")
        if decide:
            if result is not None:
                # Computed without the judgment it says it needs: not a result to show.
                raise FunctionFailed("contract", "compute asked for a decision and gave a result anyway")
            for name in decide:
                if value_at(input, name) is not None:
                    raise FunctionFailed("contract", f"compute.decide: {name!r} is already decided")
            return Computed(decide=decide, assumptions=_texts(out.get("assumptions", []), "compute.assumptions"))
        if not (isinstance(result, dict) or (result is None and questions)):
            raise FunctionFailed("contract", "compute returned neither a result nor a question")
        return Computed(
            result=None if questions else result,
            questions=questions,
            assumptions=_texts(out.get("assumptions", []), "compute.assumptions"),
            waiting_for=() if questions else _waits(out.get("waiting_for", []), input, self.forms["input"]),
        )

    def check(self, input: dict, settings: dict, result: dict, function: str = "check") -> list[str]:
        out = self._call(function, {"case": input, "result": result}, settings)
        return list(_texts(out, "check"))

    def drafts(self, input: dict, settings: dict, result: dict, function: str = "letters") -> list[Draft]:
        if function not in self.functions:
            return []
        out = self._call(function, {"case": input, "result": result}, settings)
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


_WAIT_FIELDS = {"fact", "what", "from", "due"}


def _waits(value: Any, input: dict, schema: dict) -> tuple[dict, ...]:
    """What a result waits for, as the platform will hold the run on it: each item names a field
    of the input that is empty now, what it is, who supplies it and by when. A function that
    waits for a field it already has, or for one nobody can fill, would hold a run forever."""
    if not isinstance(value, list):
        raise FunctionFailed("contract", "compute.waiting_for is not a list")
    items: dict[str, dict] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != _WAIT_FIELDS
            or not all(isinstance(v, str) and v.strip() for v in item.values())
        ):
            raise FunctionFailed("contract", "compute.waiting_for: each item names fact, what, from and due")
        try:
            date.fromisoformat(item["due"])
        except ValueError:
            raise FunctionFailed("contract", f"compute.waiting_for: {item['due']!r} is not a date") from None
        if field_schema(schema, item["fact"]) is None:
            raise FunctionFailed("contract", f"compute.waiting_for: the input has no field {item['fact']!r}")
        if value_at(input, item["fact"]) is not None:
            raise FunctionFailed("contract", f"compute.waiting_for: {item['fact']!r} is already filled")
        items.setdefault(item["fact"], dict(item))
    return tuple(items.values())


def field_schema(schema: dict, path: str) -> dict | None:
    """The schema of the input field at a dotted path (`heating.hot_water_share`,
    `payments.2.amount`), or None when the form has no such field."""
    node = schema
    for part in path.split("."):
        if node.get("type") == "array" and part.isdigit():
            node = node.get("items") or {}
        elif node.get("type") == "object" and part in (node.get("properties") or {}):
            node = node["properties"][part]
        else:
            return None
    return node if node.get("type") not in ("object", "array") else None


def value_at(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def with_value(data: dict, path: str, value: Any) -> dict:
    """A copy of `data` with `value` at `path`; the path must lead to an existing place."""
    out = copy.deepcopy(data)
    *parents, last = path.split(".")
    node: Any = out
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node.get(part)
        if not isinstance(node, (dict, list)):
            raise KeyError(path)
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return out


def _texts(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise FunctionFailed("contract", f"{what} is not a list of texts")
    return tuple(value)
