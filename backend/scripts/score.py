"""Score an answer against an expert's scoring sheet (ADR-0042 D1). Calls the judge model.

    # another agent's answer
    python -m scripts.score --sheet evals/nk04.sheet.yaml --answer-pdf "NK-Test-04-Antwort.pdf"
    # a stored platform result (a trial's snapshot.json, one case of it)
    python -m scripts.score --sheet evals/nk04.sheet.yaml --snapshot trial/snapshot.json --case nk04
    # the platform end to end: the task's documents read by the reader, then computed, one case
    # per unit they cover
    python -m scripts.score --sheet evals/nk04.sheet.yaml --template betriebskosten --doc "NK-Test-04 Auftrag.pdf" \
        --about "Wohnung Nr. 2 · 2024" --about "Wohnung Nr. 4 · 2024"

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
from app.assess.model import AssessUnavailable, assess_model
from app.assess.service import AssessmentRefused, assess_case
from app.db.models import WorkflowAgent, WorkflowCase
from app.workflow_agents import runs
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
    workflow = workflow_from(content, bundles_dir())
    answers = []
    # One case per unit the task covers (NK-04: two flats of one landlord), each named to the reader.
    for about in args.about or [""]:
        data = await OpenAIReader(settings.openai_api_key, settings.extraction_model).read(
            strict(content["forms"]["input"]), content["guide"], docs, about=about
        )
        answer = render_platform(await _end_to_end(workflow, data, docs))
        answers.append(f"=== Fall: {about} ===\n{answer}" if about else answer)
    return "\n\n".join(answers)


async def _end_to_end(workflow, data: dict, docs: list[Document]) -> dict:
    """The platform's whole path for one case: the input read, the run, its assessment by the model
    when the definition assesses (the task's documents as the case's files), the rest computed."""
    tenant = uuid.uuid4()
    case = WorkflowCase(id=uuid.uuid4(), tenant_id=tenant, agent_id=uuid.uuid4(), title="score", input=data,
                        input_revision=1, errors=[], questions=[], assumptions=[], problems=[], drafts=[])
    agent = WorkflowAgent(id=case.agent_id, tenant_id=tenant, settings=SETTINGS)
    record = runs.run_case(case, agent, workflow)
    if (step := runs.assessing(record)) is not None:
        try:
            assessment, trace = await assess_case(assess_model(), workflow, step, data, SETTINGS, docs)
            runs.complete_assessment(case, agent, workflow, record, assessment, trace)
        except (AssessUnavailable, AssessmentRefused) as exc:
            runs.fail_assessment(case, record, str(exc))
    outcome = {
        "status": case.status, "result": case.result, "letters": case.drafts,
        "waiting_for": [w["fact"] for w in runs.awaited(record)], "deciding": [d["field"] for d in runs.to_decide(record)],
        "assumptions": case.assumptions, "problems": case.problems, "questions": case.questions, "errors": case.errors,
    }
    if "assess" in record.outputs:
        outcome["assessment"] = record.outputs["assess"].get("assessment")
    return outcome


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
    p.add_argument("--about", action="append", default=[], help="a case's title, one per unit the documents cover")
    p.add_argument("--out")
    p.add_argument("--rounds", type=int, default=3, help="how often the judge judges; the majority counts")
    asyncio.run(main(p.parse_args()))
