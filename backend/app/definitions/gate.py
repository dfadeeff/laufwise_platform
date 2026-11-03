"""The publish gate runs a definition's cases (ADR-0039 D4 and step 4): no FastAPI.

A version is published only if every one of its cases comes out as the case expects, run the way
a real case runs: through the procedure, each function in the sandbox, with the platform's check
and letter check. A definition with no case, or with no counter-check against an answer held back
or hedged, is not published either (ADR-0038 D4). The report is sealed with the version.

`guardrails` is the panel the Studio shows for an agent: each guardrail as a plain question, its
answer for this version, what enforces it, and whether anyone can switch it off.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone

from app.db.models import WorkflowAgent, WorkflowCase
from app.kernel.cases import compare
from app.kernel.definitions import DefinitionError
from app.kernel.procedures import judgment_fields
from app.kernel.workflows import Workflow
from app.workflow_agents.runs import awaited, run_case, to_decide


class GateRefused(DefinitionError):
    """The version's cases did not all pass; `report` says which and why. Nothing was published."""

    def __init__(self, key: str, report: dict) -> None:
        lines = [report["refused"]] if report.get("refused") else []
        lines += [f"{c['name']}: {f}" for c in report["cases"] for f in c["failures"]]
        super().__init__(f"{key} is not published: " + "; ".join(lines))
        self.report = report


def run_gate(workflow: Workflow, content: dict) -> dict:
    """Every case of `content`, run on `workflow` (built from that content). Never raises for a
    failing case; the report says what failed."""
    cases = content.get("tests") or []
    report = {"at": datetime.now(timezone.utc).isoformat(), "cases": []}
    if not cases:
        report["refused"] = "it has no test cases; a person chooses which, but there must be some"
    elif not any(c.get("counter") for c in cases):
        report["refused"] = "none of its cases has a counter-check against an answer held back or hedged"
    for case in cases:
        failures = compare(case, _run(workflow, case))
        report["cases"].append({
            "name": case["name"], "source": case["source"], "counters": len(case.get("counter") or []),
            "passed": not failures, "failures": failures,
        })
    report["passed"] = "refused" not in report and all(c["passed"] for c in report["cases"])
    return report


def require(workflow: Workflow, content: dict) -> dict:
    report = run_gate(workflow, content)
    if not report["passed"]:
        raise GateRefused(content["key"], report)
    return report


def _run(workflow: Workflow, case: dict) -> dict:
    tenant = uuid.uuid4()
    held = WorkflowCase(
        id=uuid.uuid4(), tenant_id=tenant, agent_id=uuid.uuid4(), title=case["name"],
        input=copy.deepcopy(case["input"]), input_revision=1,
        errors=[], questions=[], assumptions=[], problems=[], drafts=[],
    )
    agent = WorkflowAgent(id=held.agent_id, tenant_id=tenant, settings=copy.deepcopy(case.get("settings") or {}))
    run = run_case(held, agent, workflow)
    return {
        "status": held.status,
        "result": held.result,
        "letters": held.drafts,
        "waiting_for": [w["fact"] for w in awaited(run)],
        "deciding": [d["field"] for d in to_decide(run)],
        "assumptions": held.assumptions,
        "problems": held.problems,
        "questions": held.questions,
        "errors": held.errors,
    }


def guardrails(workflow: Workflow) -> list[dict]:
    """The guardrail panel for one published version (ADR-0039 D4)."""
    checked = [s.of for s in workflow.procedure if s.kind == "check"]
    judgments = sorted(judgment_fields(workflow.procedure))
    gate = workflow.gate
    if gate is None:
        tests = "Not run: this version was read from files, not published."
    else:
        names = ", ".join(c["name"] for c in gate["cases"])
        counters = sum(c["counters"] for c in gate["cases"])
        tests = (
            f"{len(gate['cases'])} cases ({names}) with {counters} counter-checks, all passed when it "
            f"was published on {gate['at'][:10]}."
        )
    return [
        {
            "question": "Is every function's result checked?",
            "answer": f"Yes: a check runs after {', '.join(checked)}, and a result it refuses is never shown.",
            "enforced_by": "the procedure's shape, refused at publish otherwise",
            "can_switch_off": False,
        },
        {
            "question": "Can a letter say anything the result does not?",
            "answer": "No: every amount in a letter and its enclosure must be in the result.",
            "enforced_by": "the letter check",
            "can_switch_off": False,
        },
        {
            "question": "Who approves before anything leaves the workspace?",
            "answer": "A second administrator of the workspace; who asked never decides.",
            "enforced_by": "the approval gate",
            "can_switch_off": False,
        },
        {
            "question": "Which tests must pass before publishing?",
            "answer": tests,
            "enforced_by": "the publish gate, which runs every case, counter-checks included",
            "can_switch_off": False,
        },
        {
            "question": "Which judgments are a person's?",
            "answer": (
                f"{', '.join(judgments)}: decided in the case, with a reason; never typed into the input or read by a model."
                if judgments
                else "None in this agent."
            ),
            "enforced_by": "the decide step",
            "can_switch_off": False,
        },
        {
            "question": "Which rules version is active?",
            "answer": workflow.rules_version,
            "enforced_by": "the published version, which never changes",
            "can_switch_off": False,
        },
        {
            "question": "What may the agent touch outside the platform?",
            "answer": "Nothing: it only computes. Letters leave only through the approval gate.",
            "enforced_by": "the sandbox: no network, no files, no clock",
            "can_switch_off": False,
        },
    ]
