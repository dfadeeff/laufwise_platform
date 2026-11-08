"""A definition's test cases, as data (ADR-0039 step 4; ADR-0038 D4).

    definitions/<module>/<key>/cases/<name>.yaml

    source:   where the expected values come from (an expert's sheet, a norm, by hand)
    input:    the case's input, or `input_file:` a YAML file in the definition's directory
    settings: the agent's settings it is run with
    expect:
      status:      ready | waiting | deciding | blocked | draft | failed
      result:      {path: {value, ref, tolerance?, unverified?}}  each figure names where it
                   was copied from; one the builder proposed is `unverified` until a person
                   confirms it against that source, and no version is published before
      letters:     [recipient, ...]                   exactly these, in this order
      waiting_for: [input field, ...]                 exactly these facts awaited
      deciding:    [input field, ...]                 exactly these judgments asked
    sealed:       true: hidden from the builder (an expert's sheet); run by the gate like any other
    counter:      checks against an answer held back or hedged, each with `why`
      - {no_letter_matches: regex}
      - {every_letter_matches: regex}
      - {some_letter_matches: regex}       at least one letter (with its enclosure) says it
      - {no_assumption_matches: regex}

A path reads the result: `deadline`, `proposal.proposed`, `statements.0.total`, or by a field's
value, `statements[party=Herr Özdemir].balance`.

Cases are part of a version's content: a published version names exactly the cases it passed.
They are run by the platform, like any case, through the sandbox (`app.definitions.gate`); this
module only checks their shape and compares what a run came to with what a case expects.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

STATUSES = ("ready", "waiting", "deciding", "blocked", "draft", "failed")
COUNTERS = ("no_letter_matches", "every_letter_matches", "some_letter_matches", "no_assumption_matches")
_EXPECT = {"status", "result", "letters", "waiting_for", "deciding"}
_SEGMENT = re.compile(r"^([^\[\]]+)(?:\[([^=\]]+)=([^\]]*)\])?$")


class CaseError(ValueError):
    """A test case the platform cannot run as it stands."""


def check_cases(cases: Any) -> None:
    if not isinstance(cases, list):
        raise CaseError("tests are a list of cases")
    names = [c.get("name") for c in cases if isinstance(c, dict)]
    if len(set(names)) != len(cases):
        raise CaseError("every case has a name of its own")
    for case in cases:
        _check_case(case)


def _check_case(case: dict) -> None:
    name = case["name"]
    if not isinstance(case.get("source"), str) or not case["source"].strip():
        # ADR-0038 D4: a file without a source does not count as proof.
        raise CaseError(f"case {name!r} names no source for what it expects")
    if not isinstance(case.get("input"), dict) or not isinstance(case.get("settings", {}), dict):
        raise CaseError(f"case {name!r} has no input")
    if not isinstance(case.get("sealed", False), bool):
        raise CaseError(f"case {name!r}: sealed is true or false")
    expect = case.get("expect")
    if not isinstance(expect, dict) or expect.get("status") not in STATUSES or not set(expect) <= _EXPECT:
        raise CaseError(f"case {name!r} expects a status, one of {', '.join(STATUSES)}")
    for path, figure in (expect.get("result") or {}).items():
        if not isinstance(figure, dict) or "value" not in figure or not str(figure.get("ref") or "").strip():
            raise CaseError(f"case {name!r}: {path} names the value and where it was copied from (ref)")
        resolve_path(path)  # refuses a path it cannot read
    for key in ("letters", "waiting_for", "deciding"):
        if key in expect and not (isinstance(expect[key], list) and all(isinstance(v, str) for v in expect[key])):
            raise CaseError(f"case {name!r}: {key} is a list of names")
    for counter in case.get("counter") or []:
        kinds = [k for k in COUNTERS if k in (counter if isinstance(counter, dict) else {})]
        if len(kinds) != 1 or not str(counter.get("why") or "").strip():
            raise CaseError(f"case {name!r}: each counter-check is one of {', '.join(COUNTERS)}, with why")
        try:
            re.compile(counter[kinds[0]])
        except (re.error, TypeError) as exc:
            raise CaseError(f"case {name!r}: {counter[kinds[0]]!r} is not a pattern: {exc}") from None


def unverified(cases: list[dict]) -> list[str]:
    """Expected figures no person has confirmed against their source yet, as "case: path"."""
    return [
        f"{c['name']}: {path}"
        for c in cases
        for path, figure in ((c.get("expect") or {}).get("result") or {}).items()
        if isinstance(figure, dict) and figure.get("unverified")
    ]


def resolve_path(path: str) -> list[tuple[str, str | None, str | None]]:
    """A path as its segments: (name, select field, select value)."""
    segments = []
    for part in _split(path):
        m = _SEGMENT.match(part)
        if not m:
            raise CaseError(f"{path!r} is not a path into the result")
        segments.append((m.group(1), m.group(2), m.group(3)))
    return segments


def _split(path: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for ch in path:
        depth += ch == "["
        depth -= ch == "]"
        if ch == "." and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [*parts, current]


_MISSING = object()


def read(data: Any, path: str) -> Any:
    for name, field, wanted in resolve_path(path):
        if isinstance(data, list) and name.isdigit():
            data = data[int(name)] if int(name) < len(data) else _MISSING
        elif isinstance(data, dict):
            data = data.get(name, _MISSING)
        else:
            data = _MISSING
        if data is not _MISSING and field is not None:
            found = [x for x in data if isinstance(x, dict) and str(x.get(field)) == wanted] if isinstance(data, list) else []
            data = found[0] if len(found) == 1 else _MISSING
        if data is _MISSING:
            return _MISSING
    return data


def _same(actual: Any, figure: dict) -> bool:
    want = figure["value"]
    try:
        return abs(Decimal(str(actual)) - Decimal(str(want))) <= Decimal(str(figure.get("tolerance", "0")))
    except (InvalidOperation, ValueError):
        return str(actual) == str(want) if not isinstance(want, bool) else actual is want


def compare(case: dict, outcome: dict) -> list[str]:
    """What a run came to against what the case expects. `outcome`: status, result, letters
    (drafts), waiting_for and deciding (fields), assumptions."""
    expect, failures = case["expect"], []
    if outcome["status"] != expect["status"]:
        detail = "; ".join(outcome.get("problems") or outcome.get("questions") or outcome.get("errors") or [])
        failures.append(f"status {outcome['status']}, expected {expect['status']}" + (f" ({detail})" if detail else ""))
    result = outcome.get("result") or {}
    for path, figure in (expect.get("result") or {}).items():
        actual = read(result, path)
        if actual is _MISSING:
            failures.append(f"{path}: not in the result, expected {figure['value']} ({figure['ref']})")
        elif not _same(actual, figure):
            failures.append(f"{path}: {actual}, expected {figure['value']} ({figure['ref']})")
    letters = outcome.get("letters") or []
    if "letters" in expect and [d["recipient"] for d in letters] != expect["letters"]:
        failures.append(f"letters to {[d['recipient'] for d in letters]}, expected {expect['letters']}")
    for key in ("waiting_for", "deciding"):
        if key in expect and outcome.get(key, []) != expect[key]:
            failures.append(f"{key} {outcome.get(key, [])}, expected {expect[key]}")
    for counter in case.get("counter") or []:
        failures += _counter(counter, letters, outcome.get("assumptions") or [])
    return failures


def _counter(counter: dict, letters: list[dict], assumptions: list[str]) -> list[str]:
    why = counter["why"]
    if "no_letter_matches" in counter:
        pattern = re.compile(counter["no_letter_matches"])
        return [
            f"{why}: the letter to {d['recipient']} says {m.group(0)!r}"
            for d in letters
            if (m := pattern.search(d["text"] + "\n" + d.get("enclosure", "")))
        ]
    if "every_letter_matches" in counter:
        pattern = re.compile(counter["every_letter_matches"])
        missing = [d["recipient"] for d in letters if not pattern.search(d["text"])]
        return [f"{why}: the letter to {r} does not" for r in missing] + ([f"{why}: there are no letters"] if not letters else [])
    if "some_letter_matches" in counter:
        pattern = re.compile(counter["some_letter_matches"])
        found = any(pattern.search(d["text"] + "\n" + d.get("enclosure", "")) for d in letters)
        return [] if found else [f"{why}: no letter does"]
    pattern = re.compile(counter["no_assumption_matches"])
    return [f"{why}: assumes {a!r}" for a in assumptions if pattern.search(a)]
