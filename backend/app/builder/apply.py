"""Applying what the builder proposes to a draft's content (ADR-0039 D6): pure, no I/O.

The builder answers with a change: sections to set, files to write or delete, cases to add or
replace or remove (the format is in `guide.md`). This module applies it and holds the builder to
its rules where code can:

- It sets only a definition's own parts. Key, module and version are the platform's; the module's
  `shared/` code is not one definition's to change.
- It fixes code where the code is: a file it writes may not set a name of another module
  (`calculate._split = …`). With the real model, a "fix" that swapped a copy of a broken
  function in over the original passed every case and left the bug where it was.
- Every expected figure it adds or changes is unverified, whatever it says: a person confirms it
  against the case's source before a version can be published (ADR-0038 D4). A figure it leaves
  exactly as it was keeps whatever a person already confirmed.

What it writes is not otherwise trusted here: the platform checks the content, runs the code only
in the sandbox, and publishes only through the gate.
"""

from __future__ import annotations

import ast
import copy
from typing import Any

SETTABLE = ("label", "summary", "guide", "forms", "rules", "functions", "procedure")
_ANSWER = {"summary", "set", "files", "cases", "remove_cases"}
_CONFIRMATION = ("unverified", "confirmed_by")


class ProposalError(ValueError):
    """The builder's answer is not a change the platform can apply; it is told why."""


def apply(content: dict, proposal: Any) -> tuple[dict, list[str]]:
    """The content with the proposal applied, and what changed, in words."""
    if not isinstance(proposal, dict) or not set(proposal) <= _ANSWER:
        raise ProposalError(f"answer with one object of {sorted(_ANSWER)}")
    out, changed = copy.deepcopy(content), []

    for name, value in (proposal.get("set") or {}).items():
        if name not in SETTABLE:
            raise ProposalError(f"set.{name}: only {', '.join(SETTABLE)} can be set")
        if out.get(name) != value:
            out[name] = value
            changed.append(name)

    sources = dict(out.get("sources") or {})
    for path, text in (proposal.get("files") or {}).items():
        if not isinstance(path, str) or not path.endswith(".py") or path.startswith("shared/"):
            raise ProposalError(f"files.{path}: a .py file of this definition, not shared/")
        if text is None:
            if sources.pop(path, None) is not None:
                changed.append(f"deleted {path}")
        elif not isinstance(text, str):
            raise ProposalError(f"files.{path}: the file's whole text")
        elif sources.get(path) != text:
            _sets_no_other_module(path, text)
            sources[path] = text
            changed.append(f"wrote {path}")
    out["sources"] = sources

    before = {c["name"]: c for c in content.get("tests") or [] if isinstance(c, dict) and "name" in c}
    cases = {c["name"]: c for c in copy.deepcopy(content.get("tests") or []) if isinstance(c, dict) and "name" in c}
    for name in proposal.get("remove_cases") or []:
        if cases.pop(name, None) is not None:
            changed.append(f"removed case {name}")
    for case in proposal.get("cases") or []:
        if not isinstance(case, dict) or not isinstance(case.get("name"), str):
            raise ProposalError("cases: each case is an object with a name")
        case = _held_to_its_source(copy.deepcopy(case), before.get(case["name"]))
        if cases.get(case["name"]) != case:
            changed.append(f"{'changed' if case['name'] in cases else 'added'} case {case['name']}")
            cases[case["name"]] = case
    if "tests" in out or cases:
        out["tests"] = list(cases.values())
    return out, changed


def _sets_no_other_module(path: str, text: str) -> None:
    """Refuse code that changes another module from outside: assigning to, deleting, or setattr
    on a name the file imported."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ProposalError(f"files.{path}: not Python ({exc.msg}, line {exc.lineno})") from exc
    imported = {
        (alias.asname or alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    def root(node: ast.AST) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("setattr", "delattr"):
            targets = [ast.Attribute(value=node.args[0], attr="?")] if node.args else []
        for target in targets:
            for t in target.elts if isinstance(target, ast.Tuple) else [target]:
                name = root(t)
                if isinstance(t, (ast.Attribute, ast.Subscript)) and name in imported:
                    raise ProposalError(
                        f"files.{path}, line {node.lineno}: changes {name}, another module, from "
                        "outside; fix it in the file that defines it"
                    )


def _bare(figure: Any) -> Any:
    return {k: v for k, v in figure.items() if k not in _CONFIRMATION} if isinstance(figure, dict) else figure


def _held_to_its_source(case: dict, before: dict | None) -> dict:
    expected = (case.get("expect") or {}).get("result")
    if not isinstance(expected, dict):
        return case
    old = ((before or {}).get("expect") or {}).get("result") or {}
    for path, figure in expected.items():
        if not isinstance(figure, dict):
            continue
        if path in old and _bare(old[path]) == _bare(figure):
            expected[path] = copy.deepcopy(old[path])  # untouched: as a person left it
        else:
            figure.pop("confirmed_by", None)
            figure["unverified"] = True
    return case
