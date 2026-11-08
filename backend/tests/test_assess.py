"""The assess step (ADR-0042 D2, D3): a model assesses a case with tool calls, held to its sources.

A small definition, built here: a tool that adds two amounts in the sandbox, a law text of one
sentence, and a compute that only reports what the assessment says. What is proved: where an assess
step may stand, that a run is held for its assessment and goes on with it, that a case carrying one
runs straight through (the gate's way), that the model's tool calls run in the sandbox and are kept,
and that a quote not found in its source is corrected once and then refused.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.assess.model import AssessUnavailable, OpenAIAssessor
from app.assess.service import AssessmentRefused, _lease_free, assess_case, unfound
from app.db.models import WorkflowAgent, WorkflowCase
from app.kernel.definitions import DefinitionError, bundles_dir, check_content, workflow_from
from app.workflow_agents import runs

LAW = "§ 1 Umlage. Die Kosten werden nach dem Verhältnis der Wohnfläche verteilt."
CONTENT = {
    "key": "probe", "module": "demo", "version": 1, "label": "Probe", "summary": "Probe.", "guide": "",
    "functions": ["compute", "check"],
    "forms": {
        "input": {"type": "object", "properties": {"note": {"type": "string"}}},
        "settings": {"type": "object", "properties": {}},
        "result": {"type": "object", "properties": {"charged": {"type": "string"}}},
        "assessment": {"type": "object", "properties": {
            "total": {"type": "string"},
            "basis": {"type": "array", "items": {"type": "object", "properties": {
                "source": {"type": "string"}, "quote": {"type": "string"}}}},
        }},
    },
    "rules": {"version": 1}, "rules_name": "probe",
    "sources": {
        "functions/compute.py": (
            "def run(input, settings, rules):\n"
            "    a = input.get('assessment') or {}\n"
            "    return {'result': {'charged': a.get('total', 'none')}, 'questions': [], 'assumptions': []}\n"
        ),
        "functions/check.py": "def run(input, settings, rules):\n    return []\n",
        "functions/tool_add.py": (
            "from decimal import Decimal\n\n"
            "def run(input, settings, rules):\n"
            "    return {'sum': str(Decimal(input['a']) + Decimal(input['b']))}\n"
        ),
    },
    "tools": [{
        "name": "add", "description": "Adds two amounts, as decimal text.", "function": "tool_add",
        "parameters": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
    }],
    "procedure": [
        {"id": "assess", "kind": "assess", "tools": ["add"]},
        {"id": "compute", "kind": "function", "function": "compute"},
        {"id": "check", "kind": "check", "function": "check", "of": "compute"},
    ],
    "references": [{"name": "musterv", "title": "MusterV", "cites": ["MusterV"], "url": "u", "stand": "2025",
                    "retrieved": "2026-11-08", "text": LAW}],
}
GOOD = {"total": "3.00", "basis": [{"source": "MusterV", "quote": "nach dem Verhältnis der Wohnfläche"}]}


def _content(**changes) -> dict:
    return {**copy.deepcopy(CONTENT), **changes}


def _case(input: dict) -> tuple[WorkflowCase, WorkflowAgent]:
    tenant = uuid.uuid4()
    case = WorkflowCase(id=uuid.uuid4(), tenant_id=tenant, agent_id=uuid.uuid4(), title="t", input=input,
                        input_revision=1, errors=[], questions=[], assumptions=[], problems=[], drafts=[])
    return case, WorkflowAgent(id=case.agent_id, tenant_id=tenant, settings={})


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (lambda c: c["procedure"].insert(3, {"id": "again", "kind": "assess"}), "assesses a case once"),
        (lambda c: c["procedure"].append(c["procedure"].pop(0)), "comes after a function step"),
        (lambda c: c["procedure"][0].update(tools=["divide"]), "does not declare"),
        (lambda c: c["forms"].pop("assessment"), "has none"),
        (lambda c: c["tools"][0].update(function="compute"), "not compute, check or letters"),
        (lambda c: c["tools"][0].update(parameters={"type": "string"}), "object schema"),
    ],
)
def test_a_definition_that_assesses_wrongly_is_refused(change, refused) -> None:
    content = _content()
    change(content)
    with pytest.raises(DefinitionError, match=refused):
        check_content(content)


def test_a_run_is_held_for_its_assessment_and_goes_on_with_it() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    case, agent = _case({"note": "x"})
    record = runs.run_case(case, agent, workflow)
    assert (case.status, record.state, runs.assessing(record)) == ("assessing", "suspended", "assess")
    assert case.result is None

    trace = [{"tool": "add", "arguments": {"a": "1", "b": "2"}, "result": {"sum": "3"}}]
    runs.complete_assessment(case, agent, workflow, record, GOOD, trace)
    assert (case.status, record.state, case.result) == ("ready", "succeeded", {"charged": "3.00"})
    assert record.outputs["assess"] == {"assessment": GOOD, "by": "model", "tools": trace}
    with pytest.raises(ValueError, match="not held for an assessment"):
        runs.complete_assessment(case, agent, workflow, record, GOOD, [])


def test_a_case_carrying_its_assessment_runs_straight_through_and_the_gate_requires_one() -> None:
    from app.definitions.gate import run_gate

    workflow = workflow_from(_content(), bundles_dir())
    case, agent = _case({"note": "x", "assessment": GOOD})
    record = runs.run_case(case, agent, workflow)
    assert (case.status, case.result, record.outputs["assess"]["by"]) == ("ready", {"charged": "3.00"}, "input")

    tests = [{"name": "c", "source": "s", "input": {"note": "x"}, "expect": {"status": "ready"},
              "counter": [{"no_letter_matches": "x", "why": "y"}]}]
    report = run_gate(workflow, _content(tests=tests))
    assert "carries no assessment" in report["cases"][0]["failures"][0]


def test_a_failed_assessment_ends_the_run_and_says_why() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    case, agent = _case({"note": "x"})
    record = runs.run_case(case, agent, workflow)
    runs.fail_assessment(case, record, "the model did not answer")
    assert (case.status, record.state, record.outcome) == ("failed", "failed", "failed")
    assert case.problems == ["Die Einschätzung ist fehlgeschlagen: the model did not answer"]


def _openai(responses: list[dict], seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=responses.pop(0))
    return httpx.MockTransport(handler)


def test_the_model_calls_a_tool_that_runs_in_the_sandbox_and_every_call_is_kept() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    seen: list[dict] = []
    transport = _openai([
        {"output": [{"type": "function_call", "call_id": "c1", "name": "add", "arguments": '{"a": "1.50", "b": "1.50"}'}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(GOOD)}]}]},
    ], seen)
    model = OpenAIAssessor("k", "gpt-5.5", transport)

    assessment, trace = asyncio.run(assess_case(model, workflow, "assess", {"note": "x"}, {}, []))
    assert assessment == GOOD
    assert trace == [{"tool": "add", "arguments": {"a": "1.50", "b": "1.50"}, "result": {"sum": "3.00"}}]
    assert [t["name"] for t in seen[0]["tools"]] == ["add"]  # only the step's own tools
    outputs = [i for i in seen[1]["input"] if i.get("type") == "function_call_output"]
    assert outputs == [{"type": "function_call_output", "call_id": "c1", "output": '{"result": {"sum": "3.00"}}'}]
    assert seen[0]["text"]["format"]["name"] == "assessment"


def test_a_call_to_a_tool_the_step_does_not_name_is_told_to_the_model_not_run() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    seen: list[dict] = []
    transport = _openai([
        {"output": [{"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{}"}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(GOOD)}]}]},
    ], seen)
    _, trace = asyncio.run(assess_case(OpenAIAssessor("k", "m", transport), workflow, "assess", {}, {}, []))
    assert "is not a tool of this step" in trace[0]["error"]
    assert "error_message" in json.loads(seen[1]["input"][-1]["output"])


def test_an_assessment_that_does_not_end_fails() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    call = {"output": [{"type": "function_call", "call_id": "c", "name": "add", "arguments": '{"a": "1", "b": "1"}'}]}
    transport = _openai([copy.deepcopy(call) for _ in range(40)], [])
    with pytest.raises(AssessUnavailable, match="tool calls"):
        asyncio.run(assess_case(OpenAIAssessor("k", "m", transport), workflow, "assess", {}, {}, []))


class Scripted:
    model = "scripted"

    def __init__(self, *answers) -> None:
        self.answers, self.texts = list(answers), []

    async def assess(self, system, text, documents, tools, form, call):
        self.texts.append(text)
        return self.answers.pop(0), []


def test_a_quote_not_in_its_source_is_shown_to_the_model_once_then_refused() -> None:
    workflow = workflow_from(_content(), bundles_dir())
    invented = {"total": "3.00", "basis": [{"source": "MusterV", "quote": "nach Köpfen verteilt"}]}
    assert unfound(invented, {"MusterV": LAW}) == ["MusterV: 'nach Köpfen verteilt' is not in it, word for word"]
    assert "is not one of the sources" in unfound({"x": {"source": "BGB", "quote": "a"}}, {"MusterV": LAW})[0]

    corrected = Scripted(invented, GOOD)
    assert asyncio.run(assess_case(corrected, workflow, "assess", {}, {}, []))[0] == GOOD
    assert "zitierte, was in den Quellen nicht steht" in corrected.texts[1]
    with pytest.raises(AssessmentRefused, match="nach Köpfen"):
        asyncio.run(assess_case(Scripted(invented, invented), workflow, "assess", {}, {}, []))


def test_a_claimed_assessment_is_taken_over_only_once_its_lease_has_run_out() -> None:
    # A worker that crashed mid-assessment leaves its claim; after the lease, the next sweep restarts it.
    now = datetime.now(timezone.utc)
    assert _lease_free({"pending": True}, now)
    assert not _lease_free({"pending": True, "claimed_at": (now - timedelta(minutes=5)).isoformat()}, now)
    assert _lease_free({"pending": True, "claimed_at": (now - timedelta(minutes=16)).isoformat()}, now)
