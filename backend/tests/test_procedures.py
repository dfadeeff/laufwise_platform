"""A definition's procedure and the run through it (ADR-0039 D3, step 3a).

The shape is a guardrail: every function step is followed by its check, and letters come after
the check of what they write from. A run records its state apart from its outcome, and one entry
per step it ran.
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.db.models import WorkflowAgent, WorkflowCase
from app.functions.sandbox import FunctionFailed
from app.kernel.definitions import DefinitionError, bundles_dir, check_content, read_definition, workflow_from
from app.kernel.procedures import ProcedureError, default_procedure, procedure_of
from app.kernel.workflows import Workflow
from app.workflow_agents.runs import run_case
from tests.conftest import FIXTURE_DEFINITIONS
from tests.test_workflow_agents import SETTINGS, _fixture

DEMO = FIXTURE_DEFINITIONS / "demo"


def _content(**changes) -> dict:
    return {**copy.deepcopy(read_definition(DEMO / "umlage", DEMO)), **changes}


def _steps(*raw: dict):
    return procedure_of({"functions": ["compute", "check", "letters"], "procedure": list(raw)})


F = {"id": "compute", "kind": "function", "function": "compute"}
C = {"id": "check", "kind": "check", "function": "check", "of": "compute"}
L = {"id": "letters", "kind": "letters", "function": "letters", "of": "compute"}


def test_a_definition_without_a_procedure_runs_compute_its_check_and_its_letters() -> None:
    assert [s["id"] for s in default_procedure(["compute", "check", "letters"])] == ["compute", "check", "letters"]
    assert [s["id"] for s in default_procedure(["compute", "check"])] == ["compute", "check"]
    # Derived, not stored: the content of a shipped definition does not change by having one.
    assert "procedure" not in read_definition(DEMO / "umlage", DEMO)


@pytest.mark.parametrize(
    ("steps", "refused"),
    [
        ((F, L), "not followed by its check"),  # the check removed
        ((F, L, C), "not followed by its check"),  # the check moved after the letters
        ((F, C, {**L, "of": "nothing"}), "no function step"),
        ((F, {**C, "of": "other"}), "not followed by its check"),
        ((F, C, {"id": "x", "kind": "teleport", "function": "check"}), "no step kind"),
        (({**F, "function": "undeclared"}, C), "does not declare"),
        ((F, C, {**C}), "an id of its own"),
        ((), "at least one step"),
    ],
)
def test_a_procedure_that_drops_or_moves_a_check_is_not_run(steps, refused) -> None:
    with pytest.raises(ProcedureError, match=refused):
        _steps(*steps)


def test_letters_before_the_check_of_what_they_write_from_are_refused() -> None:
    with pytest.raises(ProcedureError, match="before the check"):
        _steps(L, F, C)  # a check must follow its function at once, so only this order is left


def test_a_definition_whose_procedure_drops_its_check_cannot_be_published() -> None:
    with pytest.raises(DefinitionError, match="not followed by its check"):
        check_content(_content(procedure=[F, L]))


# --- the run, without a database: the engine on a case object -----------------------------


def _case(name: str) -> tuple[WorkflowCase, WorkflowAgent]:
    tenant = uuid.uuid4()
    case = WorkflowCase(id=uuid.uuid4(), tenant_id=tenant, agent_id=uuid.uuid4(), title="T",
                        input=_fixture(name), input_revision=3, errors=[], questions=[],
                        assumptions=[], problems=[], drafts=[])
    return case, WorkflowAgent(id=case.agent_id, tenant_id=tenant, settings=SETTINGS)


def _umlage(**changes) -> Workflow:
    return workflow_from(_content(**changes), bundles_dir())


def test_a_ready_run_records_every_step_and_its_state_apart_from_its_outcome() -> None:
    case, agent = _case("u2")
    run = run_case(case, agent, _umlage())

    assert (run.state, run.outcome, run.position) == ("succeeded", "ready", 3)
    assert [(t["step"], t["ended"]) for t in run.trace] == [("compute", None), ("check", None), ("letters", None)]
    assert case.status == "ready" and case.result == run.outputs["compute"]["result"]
    assert [d["recipient"] for d in case.drafts] == ["Herr Adler", "Frau Berg", "Firma Clausen"]
    assert run.input_revision == 3


def test_a_question_ends_the_run_at_its_step_and_nothing_after_it_runs() -> None:
    case, agent = _case("u1")
    agent.settings = {}  # no standing decision: the unclassified item is asked
    run = run_case(case, agent, _umlage())

    assert (run.state, run.outcome, run.position) == ("succeeded", "blocked", 1)
    assert [t["step"] for t in run.trace] == ["compute"]
    assert case.result is None and case.drafts == [] and case.questions


def test_a_procedure_stated_explicitly_is_the_one_run() -> None:
    case, agent = _case("u2")
    stated = [{**F, "id": "settle"}, {**C, "id": "verify", "of": "settle"}]  # no letters
    run = run_case(case, agent, _umlage(procedure=stated))

    assert [t["step"] for t in run.trace] == ["settle", "verify"]
    assert run.outcome == "ready" and case.drafts == []


def test_a_failing_function_fails_the_run_and_keeps_nothing() -> None:
    case, agent = _case("u2")

    class Failing(Workflow):
        def check(self, input, settings, result, function="check"):
            raise FunctionFailed("time", "longer than 10 s")

    real = _umlage()
    run = run_case(case, agent, Failing(**{f: getattr(real, f) for f in real.__dataclass_fields__}))

    assert (run.state, run.outcome, run.position) == ("failed", "failed", 1)
    assert run.trace[-1] == {"step": "check", "kind": "check", "failed": "time"}
    assert case.result is None and case.drafts == []
    assert case.problems == ["Die Berechnung ist fehlgeschlagen (time): longer than 10 s"]


def test_a_procedure_stated_in_the_files_is_read_and_exported_again(tmp_path) -> None:
    from app.definitions.service import export_files
    from app.kernel.definitions import checksum

    stated = _content(procedure=[{**F, "id": "settle"}, {**C, "id": "verify", "of": "settle"}])
    directory = export_files(stated, tmp_path)
    again = read_definition(directory, directory.parent)
    assert again["procedure"] == stated["procedure"] and checksum(again) == checksum(stated)
