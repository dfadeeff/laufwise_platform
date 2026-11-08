"""Applying what the builder proposes to a draft's content (ADR-0039 D6): pure, no I/O.

The builder answers with a change: sections to set, new files to write, edits to existing ones,
files to delete, cases to add or replace or remove (the format is in `guide.md`). This module applies it and holds the builder to
its rules where code can:

- It sets only a definition's own parts. Key, module and version are the platform's; the module's
  `shared/` code is not one definition's to change.
- It changes an existing file only by edits, each an exact piece of text found once and
  replaced. Whole files written back lost 650 lines of code on NK-03, the check's independent
  layer among them, and every case still passed.
- The guide (how documents are read) changes only by edits too, and a form may gain fields but
  never lose one: on NK-04 the guide set whole lost eleven reading rules and the result form lost
  three blocks the Studio shows, and every case still passed.
- A change to the check's code (whatever `functions/check.py` reaches, `reach.py`) waits for a
  person: the draft records it (`check_changed`) and the gate refuses until it is confirmed.
- It fixes code where the code is: a file it writes may not set a name of another module
  (`calculate._split = …`). With the real model, a "fix" that swapped a copy of a broken
  function in over the original passed every case and left the bug where it was.
- Every expected figure it adds or changes is unverified, whatever it says: a person confirms it
  against the case's source before a version can be published (ADR-0038 D4). A figure it leaves
  exactly as it was keeps whatever a person already confirmed.
- A case a person hid from it (`sealed`) is not its to see, change or remove: it is shown the
  content without them (`visible`), and they come back unchanged. It cannot hide a case either.

What it writes is not otherwise trusted here: the platform checks the content, runs the code only
in the sandbox, and publishes only through the gate.
"""

from __future__ import annotations

import ast
import copy
import difflib
import json
from typing import Any

from app.builder.reach import changed_in_check

SETTABLE = ("label", "summary", "guide", "forms", "rules", "functions", "procedure")
_ANSWER = {"summary", "set", "files", "edits", "cases", "remove_cases"}
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
        if name == "guide" and value != out.get("guide"):
            raise ProposalError('set.guide: the guide changes by edits, like a file: {"edits": {"guide": [...]}}')
        if name == "forms":
            _keeps_every_field(out.get("forms") or {}, value)
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
        elif path in sources and sources[path] != text:
            raise ProposalError(f"files.{path}: the file exists; change it with edits")
        elif sources.get(path) != text:
            _sets_no_other_module(path, text)
            sources[path] = text
            changed.append(f"wrote {path}")
    for path, edits in (proposal.get("edits") or {}).items():
        if path == "guide":
            text = _edited("guide", out.get("guide") or "", edits)
            if text != out.get("guide"):
                out["guide"] = text
                changed.append("edited guide")
            continue
        if path not in sources or path.startswith("shared/"):
            raise ProposalError(f"edits.{path}: no such file of this definition; a new file goes in files")
        text = _edited(path, sources[path], edits)
        if text != sources[path]:
            _sets_no_other_module(path, text)
            sources[path] = text
            changed.append(f"edited {path}")
    out["sources"] = sources
    if touched := set(changed_in_check(content.get("sources") or {}, sources)):
        out["check_changed"] = sorted(set(out.get("check_changed") or []) | touched)
        out.pop("check_confirmed_by", None)

    hidden = [c for c in content.get("tests") or [] if _sealed(c)]
    before = {c["name"]: c for c in visible(content).get("tests") or [] if isinstance(c, dict) and "name" in c}
    cases = copy.deepcopy(before)
    for name in proposal.get("remove_cases") or []:
        if cases.pop(name, None) is not None:
            changed.append(f"removed case {name}")
    for case in proposal.get("cases") or []:
        if not isinstance(case, dict) or not isinstance(case.get("name"), str):
            raise ProposalError("cases: each case is an object with a name")
        if any(h["name"] == case["name"] for h in hidden):
            raise ProposalError(f"cases.{case['name']}: a case you are not shown has that name; choose another")
        if case.get("sealed"):
            raise ProposalError(f"cases.{case['name']}: only a person hides a case from the builder")
        case = _held_to_its_source(copy.deepcopy(case), before.get(case["name"]))
        if cases.get(case["name"]) != case:
            changed.append(f"{'changed' if case['name'] in cases else 'added'} case {case['name']}")
            cases[case["name"]] = case
    if "tests" in out or cases:
        out["tests"] = list(cases.values()) + copy.deepcopy(hidden)
    return out, changed


def _edited(path: str, text: str, edits: Any) -> str:
    """`text` with each {old, new} applied in order; each old must occur exactly once."""
    if not isinstance(edits, list) or not edits:
        raise ProposalError(f"edits.{path}: a list of {{old, new}}")
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or not isinstance(edit.get("new"), str) or not edit.get("old") or not isinstance(edit["old"], str):
            raise ProposalError(f"edits.{path}[{i}]: old and new, both text, old not empty")
        if (count := text.count(edit["old"])) != 1:
            raise ProposalError(f"edits.{path}[{i}]: old occurs {count} times; it must occur exactly once")
        text = text.replace(edit["old"], edit["new"])
    return text


def _fields(schema: Any, prefix: str = "") -> set[str]:
    if not isinstance(schema, dict):
        return set()
    found = set()
    for name, sub in (schema.get("properties") or {}).items():
        found |= {prefix + name} | _fields(sub, prefix + name + ".")
    return found | _fields(schema.get("items"), prefix + "[].")


def _keeps_every_field(before: dict, after: Any) -> None:
    """A form may gain fields, never lose one: what a person enters, what the reader fills and
    what the Studio shows would go with it, and no case would notice."""
    if not isinstance(after, dict):
        raise ProposalError("set.forms: the three forms, input, settings and result")
    for name, schema in before.items():
        if dropped := sorted(_fields(schema) - _fields(after.get(name))):
            raise ProposalError(f"forms.{name}: drops {', '.join(dropped)}; keep every field, add new ones beside them")


def _sealed(case: Any) -> bool:
    return isinstance(case, dict) and case.get("sealed") is True


def visible(content: dict) -> dict:
    """The content as the builder may see it: without the cases a person hid from it."""
    if not any(_sealed(c) for c in content.get("tests") or []):
        return content
    return {**content, "tests": [c for c in content["tests"] if not _sealed(c)]}


def hidden_count(content: dict) -> int:
    return sum(_sealed(c) for c in content.get("tests") or [])


def changes(before: dict, after: dict) -> list[dict]:
    """What a run changed, part by part, as a unified diff a person reads: a passing case shows
    what a change came to, not how it was made. Hidden cases never change, and are left out."""
    before, after = visible(before), visible(after)

    def cases(content: dict) -> dict:
        return {c["name"]: c for c in content.get("tests") or [] if isinstance(c, dict) and "name" in c}

    parts: list[tuple[str, Any, Any]] = [(n, before.get(n), after.get(n)) for n in SETTABLE]
    old, new = before.get("sources") or {}, after.get("sources") or {}
    parts += [(path, old.get(path), new.get(path)) for path in sorted(set(old) | set(new))]
    old, new = cases(before), cases(after)
    parts += [(f"case {n}", old.get(n), new.get(n)) for n in sorted(set(old) | set(new))]

    out = []
    for part, a, b in parts:
        if a == b:
            continue
        lines = list(difflib.unified_diff(_lines(a), _lines(b), part, part, lineterm=""))
        body = lines[2:]
        out.append({
            "part": part,
            "added": sum(line.startswith("+") for line in body),
            "removed": sum(line.startswith("-") for line in body),
            "diff": "\n".join(lines),
        })
    return out


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    text = value if isinstance(value, str) and "\n" in value else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return text.splitlines()


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
