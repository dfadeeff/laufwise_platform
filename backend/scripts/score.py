"""Score an answer against an expert's scoring sheet (ADR-0042 D1). Calls the judge model.

    # another agent's answer
    python -m scripts.score --sheet evals/nk04.sheet.yaml --answer-pdf "NK-Test-04-Antwort.pdf"
    # a stored platform result (a trial's snapshot.json, one case of it)
    python -m scripts.score --sheet evals/nk04.sheet.yaml --snapshot trial/snapshot.json --case nk04
    # the platform end to end: the task's documents read by the reader, then computed
    python -m scripts.score --sheet evals/nk04.sheet.yaml --draft trial/draft.json --doc "NK-Test-04 Auftrag.pdf"

Prints each criterion with its verdict and the quoted evidence, the points, the knock-outs and
whether the expert would call the answer usable; `--out` writes the report and the answer scored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import yaml
from pypdf import PdfReader

from app.builder.model import OpenAIBuilder
from app.config import settings
from app.definitions.gate import _run as run_case  # the gate's own way of running a case
from app.evals.scoring import ModelJudge, render_platform, score
from app.extraction.reader import Document, OpenAIReader
from app.extraction.strict import strict
from app.kernel.definitions import DEFINITIONS, bundles_dir, read_definition, workflow_from

SETTINGS = {"basis": "unit_share", "sender": "Hausverwaltung Muster", "payment_days": 30}


def _definition(args) -> dict:
    if args.draft:
        return json.loads(Path(args.draft).read_text(encoding="utf-8"))
    module = next(p.parent.parent for p in DEFINITIONS.glob(f"*/{args.template}/definition.yaml"))
    return read_definition(module / args.template, module)


async def _answer(args) -> str:
    if args.answer_pdf:
        return "\n".join(page.extract_text() or "" for page in PdfReader(args.answer_pdf).pages)
    if args.snapshot:
        return render_platform(json.loads(Path(args.snapshot).read_text(encoding="utf-8"))["cases"][args.case])
    content = _definition(args)
    docs = [Document(Path(d).name, "application/pdf", Path(d).read_bytes()) for d in args.doc]
    data = await OpenAIReader(settings.openai_api_key, settings.extraction_model).read(
        strict(content["forms"]["input"]), content["guide"], docs
    )
    case = {"name": f"score-{uuid.uuid4().hex[:6]}", "input": data, "settings": SETTINGS}
    return render_platform(run_case(workflow_from(content, bundles_dir()), case))


async def main(args) -> None:
    sheet = yaml.safe_load(Path(args.sheet).read_text(encoding="utf-8"))
    answer = await _answer(args)
    report = await score(sheet, answer, ModelJudge(OpenAIBuilder(settings.openai_api_key, settings.judge_model)), args.rounds)
    for c in report["criteria"]:
        mark = {"met": "✓", "partly": "½", "not met": "✗"}[c["verdict"]]
        agreed = f" [{c['agreed']}]" if c.get("agreed") else ""
        print(f"{mark} {c['id']}{' (K.-o.)' if c['ko'] else ''}{agreed}: {c['reason']}")
        if c["quote"]:
            print(f"    «{c['quote'][:200]}»")
    threshold = f", usable from {report['usable_from']}" if report["usable_from"] is not None else ""
    print(f"\n{report['test']}: {report['points']:g} of {report['of']:g}{threshold}; "
          f"knock-outs not met: {', '.join(report['knocked_out']) or 'none'}; "
          f"{'USABLE' if report['usable'] else 'NOT USABLE'} (judge {settings.judge_model})")
    if args.out:
        Path(args.out).write_text(json.dumps({**report, "answer": answer}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sheet", required=True)
    p.add_argument("--answer-pdf")
    p.add_argument("--snapshot")
    p.add_argument("--case")
    p.add_argument("--draft")
    p.add_argument("--template")
    p.add_argument("--doc", action="append", default=[])
    p.add_argument("--out")
    p.add_argument("--rounds", type=int, default=3, help="how often the judge judges; the majority counts")
    asyncio.run(main(p.parse_args()))
