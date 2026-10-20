"""A workflow a module offers, run case by case (ADR-0030).

A workspace creates a *workflow agent* from it (a name and settings), and every piece of work that
agent does is a *case*: documents in, an input a person can correct, a computed result. The module
supplies four pure functions; the kernel owns everything around them: storage, revisions, the
order they run in, and that the check always runs.

    parse(input, settings) -> parsed      raises InputError: the input cannot be computed
    compute(parsed)        -> Computed     questions block the result; assumptions are stated
    check(parsed, value)   -> [problems]   the postcondition, run by the kernel on every result

A person changes the input, never the result: there is no way to write a result except by
computing it from an input, and a result that fails its check is never shown as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.kernel.schema import Choices, object_schema


class InputError(Exception):
    """The input cannot be computed; `messages` say what to correct."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Computed:
    value: Any  # the module's result object, handed back to `check`
    result: dict | None  # its JSON form; None when questions block it
    questions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    """A text the workflow writes from a result, e.g. a letter to one tenant (ADR-0033)."""

    title: str
    recipient: str
    text: str
    # A document sent with it, e.g. the itemised statement the letter says is enclosed. Checked
    # against the result exactly like the letter.
    enclosure: str = ""
    enclosure_name: str = ""


@dataclass(frozen=True)
class Workflow:
    key: str
    label: str
    summary: str
    input_type: type  # dataclasses: the kernel derives the forms from them
    settings_type: type
    result_type: type
    parse: Callable[[dict, dict], Any]
    compute: Callable[[Any], Computed]
    check: Callable[[Any, Any], list[str]]
    # The version of the rules the module computes with (e.g. its catalogue); recorded with every
    # computation, so a result can be traced to the rules that produced it.
    rules_version: str
    labels: Mapping[str, str] = field(default_factory=dict)
    choices: Choices = field(default_factory=dict)
    result_omit: tuple[str, ...] = ()  # result fields the kernel shows elsewhere (questions…)
    # How to read this workflow's documents into its input: domain knowledge for the reading model
    # (which row is which, what a document leaves out). Data, shown to the model, never executed.
    extraction_guide: str = ""
    # Texts written from a ready result (letters). The kernel checks that every euro amount in
    # them is in the result, and shows none of them, nor the result, if one is not.
    drafts: Callable[[Any, Any], list[Draft]] | None = None

    def schemas(self) -> dict:
        """The input, settings and result forms. Raises TypeError for a type with no form, which
        `installed()` turns into a boot error."""
        return {
            "input": object_schema(self.input_type, self.labels, self.choices),
            "settings": object_schema(self.settings_type, self.labels, self.choices),
            "result": object_schema(
                self.result_type, self.labels, self.choices, omit=self.result_omit
            ),
        }

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "summary": self.summary}
