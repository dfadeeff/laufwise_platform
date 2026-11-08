"""Run an expert's eval cases against a definition, as hidden cases (ADR-0039 step 6d).

    python -m scripts.run_evals --draft trial/draft.json evals/nk03-loesung.yaml evals/nk04-loesung.yaml
    python -m scripts.run_evals --template betriebskosten evals/*.yaml

An eval case is a test case written from an expert's solution sheet, never from a run: each
figure names its place in the sheet, each trap that fails a solution outright is a counter-check.
They are kept outside the public repository when the sheet is the expert's; this script reads
them from where they are, runs them the way the publish gate does, and prints what failed. No
database, no model: the definition's code runs in the sandbox.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from app.definitions.gate import run_gate
from app.kernel.cases import check_cases
from app.kernel.definitions import DEFINITIONS, bundles_dir, read_definition, workflow_from


def _definition(args) -> dict:
    if args.draft:
        return json.loads(Path(args.draft).read_text(encoding="utf-8"))
    module = next(p.parent.parent for p in DEFINITIONS.glob(f"*/{args.template}/definition.yaml"))
    return read_definition(module / args.template, module)


def _eval(path: Path) -> dict:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "input_file" in case:  # beside it, or in its definition's directory (cases/ is inside it)
        name = case.pop("input_file")
        found = next(d / name for d in (path.parent, path.parent.parent) if (d / name).is_file())
        case["input"] = json.loads(json.dumps(yaml.safe_load(found.read_text(encoding="utf-8")), default=str))
    return {"name": path.stem, **case, "sealed": True}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--draft", help="a definition's content as JSON (a trial's draft.json)")
    p.add_argument("--template", help="a shipped template's key")
    p.add_argument("evals", nargs="+")
    args = p.parse_args()
    content = _definition(args)
    evals = [_eval(Path(e)) for e in args.evals]
    check_cases(evals)
    content = {**content, "tests": evals}
    report = run_gate(workflow_from(content, bundles_dir()), content)
    for case in report["cases"]:
        outcome = "passed" if case["passed"] else f"{len(case['failures'])} failed"
        print(f"{case['name']}: {outcome}")
        for failure in case["failures"]:
            print(f"  - {failure}")


if __name__ == "__main__":
    main()
