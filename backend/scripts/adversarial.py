"""Adversarial variants of a task (ADR-0042): the task's own text with one fact changed, run end to
end (read, assessed, computed, checked), and held to plain checks, not to a judge.

A variant is a YAML file:

    id: nk01-ohne-grundsteuerbescheid
    base: "NK-Test-01 Auftrag.pdf"          # next to the variant, or under --base-dir
    why: the tax notice is gone; its amount must not be charged from memory
    edits:                                   # each `old` must stand in the task's text exactly once
      - remove: "Unterlage 2 — ..."
      - replace: ["old text", "new text"]
    cases:                                   # one per unit the task covers (`about`, as in score.py)
      - about: ""
        checks:
          - {path: result.statements.0.total, equals: "2145.00"}
          - {nowhere: "186.40"}              # not in the result, nor in the letters
          - {says: "Grundsteuer", in: [questions]}
          - {status_not: ready}
          - any: [{...}, {...}]              # one of them holds

A path walks the outcome by key and list index; a word in place of an index picks the first item
that mentions it (`result.statements.Vogel.payable`).

    python -m scripts.adversarial --template betriebskosten --base-dir ../docs/.../bo ../docs/.../adversarial/*.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pypdf import PdfReader

from app.config import settings
from app.extraction.reader import Document, OpenAIReader
from app.extraction.strict import strict
from app.kernel.definitions import DEFINITIONS, bundles_dir, read_definition, workflow_from
from scripts.score import _end_to_end

PLACES = ("questions", "assumptions", "errors", "problems", "waiting_for", "letters")


class VariantError(Exception):
    """The variant does not fit its task's text: an edit matches nowhere, or more than once."""


def varied(text: str, edits: list[dict]) -> str:
    for edit in edits:
        old, new = (edit["remove"], "") if "remove" in edit else edit["replace"]
        if (n := text.count(old)) != 1:
            raise VariantError(f"{old[:60]!r} stands {n} times in the task, not once")
        text = text.replace(old, new)
    return text


def _at(value: Any, path: str) -> Any:
    for key in path.split("."):
        if isinstance(value, list):
            if key.isdigit():
                value = value[int(key)] if int(key) < len(value) else None
            else:
                value = next((v for v in value if key in json.dumps(v, ensure_ascii=False)), None)
        elif isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _text(outcome: dict, places) -> str:
    parts = []
    for place in places:
        for item in outcome.get(place) or []:
            parts.append(item.get("text", "") + "\n" + item.get("enclosure", "") if isinstance(item, dict) else str(item))
    return "\n".join(parts)


def failures(outcome: dict, checks: list[dict]) -> list[str]:
    """Each check the outcome does not meet, said plainly."""
    out = []
    for check in checks:
        if "any" in check:
            if all(failures(outcome, [c]) for c in check["any"]):
                out.append("none of: " + " | ".join(f for c in check["any"] for f in failures(outcome, [c])))
        elif "path" in check:
            got = _at(outcome, check["path"])
            if "equals" in check and got != check["equals"]:
                out.append(f"{check['path']} is {got!r}, not {check['equals']!r}")
            if check.get("absent") and got not in (None, [], {}):
                out.append(f"{check['path']} is {got!r}, and should be absent")
        elif "nowhere" in check:
            where = json.dumps(outcome.get("result"), ensure_ascii=False) + _text(outcome, ["letters"])
            if check["nowhere"] in where:
                out.append(f"{check['nowhere']!r} appears, and must not")
        elif "says" in check:
            if not re.search(check["says"], _text(outcome, check.get("in", PLACES)), re.IGNORECASE):
                out.append(f"nothing in {check.get('in', 'the outcome')} says /{check['says']}/")
        elif "status" in check and outcome.get("status") != check["status"]:
            out.append(f"status is {outcome.get('status')!r}, not {check['status']!r}")
        elif "status_not" in check and outcome.get("status") == check["status_not"]:
            out.append(f"status is {check['status_not']!r}, and must not be")
    return out


async def run(spec: dict, task_text: str, content: dict, workflow, limit: asyncio.Semaphore) -> dict:
    doc = Document(f"{Path(spec['base']).stem} (Variante).txt", "text/plain", varied(task_text, spec["edits"]).encode())
    cases = []
    for case in spec["cases"]:
        async with limit:
            data = await OpenAIReader(settings.openai_api_key, settings.extraction_model).read(
                strict(content["forms"]["input"]), content["guide"], [doc], about=case.get("about", "")
            )
            outcome = await _end_to_end(workflow, data, [doc])
        cases.append({"about": case.get("about", ""), "failures": failures(outcome, case["checks"]), "outcome": outcome})
    return {"id": spec["id"], "why": spec.get("why", ""), "passed": not any(c["failures"] for c in cases), "cases": cases}


def _task_text(path: Path) -> str:
    if path.suffix == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8")


async def main(args) -> None:
    module = next(p.parent.parent for p in DEFINITIONS.glob(f"*/{args.template}/definition.yaml"))
    content = read_definition(module / args.template, module)
    workflow = workflow_from(content, bundles_dir())
    limit = asyncio.Semaphore(args.parallel)
    jobs = []
    for file in args.variants:
        spec = yaml.safe_load(Path(file).read_text(encoding="utf-8"))
        base = Path(args.base_dir or Path(file).parent) / spec["base"]
        jobs.append(run(spec, _task_text(base), content, workflow, limit))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for file, r in zip(args.variants, results):
        if isinstance(r, BaseException):
            print(f"! {Path(file).stem}: {type(r).__name__}: {r}")
            continue
        print(f"{'✓' if r['passed'] else '✗'} {r['id']}: {r['why']}")
        for c in r["cases"]:
            for f in c["failures"]:
                print(f"    {c['about'] or 'case'}: {f}")
    passed = sum(1 for r in results if isinstance(r, dict) and r["passed"])
    print(f"\n{passed} of {len(results)} variants passed")
    if args.out:
        kept = [r if isinstance(r, dict) else {"error": str(r)} for r in results]
        Path(args.out).write_text(json.dumps(kept, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("variants", nargs="+")
    p.add_argument("--template", required=True)
    p.add_argument("--base-dir")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--out")
    asyncio.run(main(p.parse_args()))
