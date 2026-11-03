"""A definition's procedure and the run through it (ADR-0039 D3, step 3a).

The shape is a guardrail: every function step is followed by its check, and letters come after
the check of what they write from. A run records its state apart from its outcome, and one entry
per step it ran.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db.models import WorkflowAgent, WorkflowCase
from app.functions.sandbox import FunctionFailed
from app.kernel.definitions import DefinitionError, bundles_dir, check_content, read_definition, workflow_from
from app.kernel.procedures import ProcedureError, default_procedure, procedure_of
from app.kernel.workflows import Workflow, with_value
from app.workflow_agents.runs import awaited, cancel, expire, resume_run, run_case, to_decide
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
W = {"id": "payment", "kind": "wait", "of": "compute", "resume": "compute"}
CHARGE_OR_WAIVE = [{"value": "charge", "label": "berechnen"}, {"value": "waive", "label": "erlassen"}]
D = {"id": "disputed", "kind": "decide", "of": "compute",
     "decisions": [{"field": "disputed", "question": "Werden die strittigen Positionen berechnet oder erlassen?", "options": CHARGE_OR_WAIVE}]}
F2 = {"id": "second", "kind": "function", "function": "compute"}
C2 = {"id": "second-check", "kind": "check", "function": "check", "of": "second"}


def B(cases: dict, default: str = "") -> dict:
    return {"id": "route", "kind": "branch", "of": "compute", "on": "title", "cases": cases, "default": default}


def test_a_definition_without_a_procedure_runs_compute_its_check_and_its_letters() -> None:
    assert [s["id"] for s in default_procedure(["compute", "check", "letters"])] == ["compute", "check", "letters"]
    assert [s["id"] for s in default_procedure(["compute", "check"])] == ["compute", "check"]
    # Derived, not stored: a definition that states none runs it.
    stated = read_definition(DEMO / "umlage", DEMO)
    unstated = {k: v for k, v in stated.items() if k != "procedure"}
    assert [s.id for s in procedure_of(unstated)] == ["compute", "check", "letters"]


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
        ((F, W, C), "not followed by its check"),  # a wait cannot come between a function and its check
        ((F, C, {**W, "of": "nothing"}), "no function step"),
        ((F, C, {**W, "function": "compute"}), "calls no function"),
        ((F, C, {**W, "resume": "check"}), "no function step before it"),
        ((F, C, W, {**F, "id": "later"}, {**C, "id": "c2", "of": "later"}, {**W, "id": "w2", "resume": "later2"}), "no function step before it"),
        ((F, C, {**D, "decisions": []}), "names the decisions"),
        ((F, C, {**D, "decisions": [{"field": "disputed", "question": "?", "options": CHARGE_OR_WAIVE[:1]}]}), "at least two answers"),
        ((F, C, D, {**D, "id": "again"}), "more than one decide step"),
        ((F, D, C), "not followed by its check"),
        ((F, C, {**D, "function": "compute"}), "calls no function"),
        ((F, C, B({"x": "compute"})), "no step after it"),  # only forward
        ((F, C, B({"x": "nowhere"})), "no step after it"),
        ((F, C, B({"x": "second-check"}), F2, C2), "past its function"),
        ((F, C, {**B({}), "cases": {}}), "names the value it reads"),
        # The branch can skip `second`, so the letters on its result may come unchecked.
        ((F, C, B({"a": "letters"}, default="second"), F2, C2, {**L, "of": "second"}), "before the check of 'second'"),
        ((F, C, B({"a": "end"}), L), "no path reaches step 'letters'"),
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

    assert (run.state, run.outcome, run.position) == ("succeeded", "ready", 5)
    # Nothing to decide and nothing awaited: those steps let the run straight through.
    assert [(t["step"], t["ended"]) for t in run.trace] == [
        ("compute", None), ("check", None), ("disputed", None), ("letters", None), ("payment", None)
    ]
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


# --- the wait (step 3b) --------------------------------------------------------------------

SPARKASSE = {"fact": "payments.2.amount", "what": "Zahlung von Firma Clausen", "from": "Sparkasse", "due": "2026-12-01"}


def test_a_case_missing_a_fact_is_held_with_what_stands_and_who_owes_the_rest_by_when() -> None:
    case, agent = _case("u3")
    run = run_case(case, agent, _umlage())

    assert (run.state, run.outcome, run.position) == ("suspended", None, 4)
    assert run.finished_at is None
    assert awaited(run) == [SPARKASSE]
    # The end of the due date in Germany: on 2 December a person decides what now.
    assert run.expires_at == datetime(2026, 12, 2, tzinfo=ZoneInfo("Europe/Berlin"))
    assert case.status == "waiting"
    assert [s["party"] for s in case.result["statements"]] == ["Herr Adler", "Frau Berg"]
    assert [d["recipient"] for d in case.drafts] == ["Herr Adler", "Frau Berg"]


def test_the_fact_resumes_the_run_and_a_letter_already_sent_is_not_offered_again() -> None:
    case, agent = _case("u3")
    workflow = _umlage()
    run = run_case(case, agent, workflow)
    adler = case.drafts[0]  # sent while the case waited; Frau Berg's was not

    case.input = with_value(case.input, "payments.2.amount", "100.00")
    case.input_revision += 1
    resume_run(case, agent, workflow, run, sent=[adler])

    assert (run.state, run.outcome, run.input_revision) == ("succeeded", "ready", 4)
    assert run.expires_at is None and awaited(run) == []
    assert [s["party"] for s in case.result["statements"]] == ["Herr Adler", "Frau Berg", "Firma Clausen"]
    assert [d["recipient"] for d in case.drafts] == ["Frau Berg", "Firma Clausen"]
    assert [t["step"] for t in run.trace] == [
        "compute", "check", "disputed", "letters", "payment",
        "payment", "compute", "check", "disputed", "letters", "payment",
    ]
    assert run.trace[5] == {"step": "payment", "kind": "wait", "resumed": 4}


def test_a_wait_without_resume_goes_on_at_the_next_step() -> None:
    case, agent = _case("u3")
    workflow = _umlage(procedure=[F, C, {**W, "resume": ""}, L])
    run = run_case(case, agent, workflow)
    assert case.drafts == [] and run.position == 2

    case.input = with_value(case.input, "payments.2.amount", "100.00")
    resume_run(case, agent, workflow, run, sent=[])
    # The letters are written from the result held at the wait; nothing was computed again.
    assert [t["step"] for t in run.trace][-2:] == ["payment", "letters"]
    assert [d["recipient"] for d in case.drafts] == ["Herr Adler", "Frau Berg"]


def test_only_a_held_run_resumes() -> None:
    case, agent = _case("u2")
    run = run_case(case, agent, _umlage())
    with pytest.raises(ValueError, match="not held"):
        resume_run(case, agent, _umlage(), run, sent=[])


def test_an_unattended_wait_expires_and_asks_a_person() -> None:
    case, agent = _case("u3")
    run = run_case(case, agent, _umlage())
    expire(case, run)

    assert (run.state, run.outcome, run.expires_at) == ("expired", "blocked", None)
    assert run.finished_at is not None
    assert case.status == "blocked" and case.result is None and case.drafts == []
    (question,) = case.questions
    assert "Zahlung von Firma Clausen" in question and "Sparkasse" in question and "01.12.2026" in question


def test_a_change_to_the_case_ends_a_held_run() -> None:
    case, agent = _case("u3")
    run = run_case(case, agent, _umlage())
    cancel(run)
    assert (run.state, run.outcome, run.expires_at) == ("cancelled", None, None)
    cancel(None)  # no run: nothing to end


def test_a_function_that_waits_without_a_wait_step_fails_the_run() -> None:
    case, agent = _case("u3")
    run = run_case(case, agent, _umlage(procedure=[F, C, L]))
    assert (run.state, run.outcome) == ("failed", "failed")
    assert "no wait step" in case.problems[0] and case.drafts == []


def _answering(waiting: list[dict]) -> Workflow:
    """The demo workflow, with compute answering `waiting` as what it waits for."""
    real = _umlage()

    class Answering(Workflow):
        def _call(self, name, input, settings):
            out = Workflow._call(self, name, input, settings)
            return {**out, "waiting_for": waiting} if name == "compute" else out

    return Answering(**{f: getattr(real, f) for f in real.__dataclass_fields__})


@pytest.mark.parametrize(
    ("waiting", "refused"),
    [
        ([{**SPARKASSE, "fact": "payments.0.amount"}], "already filled"),
        ([{**SPARKASSE, "fact": "payments.2.colour"}], "no field"),
        ([{**SPARKASSE, "fact": "payments"}], "no field"),  # a list, not a field one fills
        ([{**SPARKASSE, "from": " "}], "names fact, what, from and due"),
        ([{k: v for k, v in SPARKASSE.items() if k != "due"}], "names fact, what, from and due"),
        ([{**SPARKASSE, "due": "bald"}], "not a date"),
        ("Sparkasse", "not a list"),
    ],
)
def test_a_wait_without_a_fillable_field_an_addressee_or_a_date_is_refused(waiting, refused) -> None:
    case, agent = _case("u3")
    run = run_case(case, agent, _answering(waiting))
    assert (run.state, run.outcome) == ("failed", "failed")
    assert refused in case.problems[0]


# --- decide and branch (step 3c) ------------------------------------------------------------


def test_a_judgment_holds_the_run_with_no_figures_until_a_person_decides() -> None:
    case, agent = _case("u4")
    run = run_case(case, agent, _umlage())

    assert (run.state, run.outcome, run.position, run.expires_at) == ("suspended", None, 2, None)
    assert case.status == "deciding" and case.result is None and case.drafts == []
    assert to_decide(run) == D["decisions"] and awaited(run) == []
    # The function asked; the check had nothing to check, and was not run.
    assert run.trace == [
        {"step": "compute", "kind": "function", "to": "disputed"},
        {"step": "disputed", "kind": "decide", "ended": "deciding"},
    ]


@pytest.mark.parametrize(("judgment", "berg"), [("waive", "778.61"), ("charge", "838.61")])
def test_the_decision_computes_the_case_again_with_it(judgment, berg) -> None:
    case, agent = _case("u4")
    workflow = _umlage()
    run = run_case(case, agent, workflow)

    case.input = {**case.input, "disputed": judgment}
    case.input_revision += 1
    resume_run(case, agent, workflow, run, sent=[])

    assert (run.state, run.outcome, case.status) == ("succeeded", "ready", "ready")
    totals = {s["party"]: s["total"] for s in case.result["statements"]}
    assert totals["Frau Berg"] == berg
    assert [t["step"] for t in run.trace][2:4] == ["disputed", "compute"]
    assert len(case.drafts) == 3


def test_a_judgment_the_definition_does_not_declare_fails_the_run() -> None:
    case, agent = _case("u4")
    run = run_case(case, agent, _umlage(procedure=[F, C, L]))
    assert (run.state, run.outcome) == ("failed", "failed")
    assert "decide step does not name" in case.problems[0]


@pytest.mark.parametrize(
    ("answer", "refused"),
    [
        ({"result": {"title": "x", "total": "0.00", "statements": []}, "decide": ["disputed"]}, "gave a result anyway"),
        ({"result": None, "decide": "disputed"}, "not a list of texts"),
    ],
)
def test_a_function_that_asks_for_a_judgment_gives_no_result(answer, refused) -> None:
    real = _umlage()

    class Answering(Workflow):
        def _call(self, name, input, settings):
            return answer if name == "compute" else Workflow._call(self, name, input, settings)

    case, agent = _case("u4")
    run_case(case, agent, Answering(**{f: getattr(real, f) for f in real.__dataclass_fields__}))
    assert refused in case.problems[0]


def test_a_decided_judgment_is_not_asked_again() -> None:
    real = _umlage()

    class Asking(Workflow):
        def _call(self, name, input, settings):
            return {"result": None, "decide": ["disputed"]} if name == "compute" else Workflow._call(self, name, input, settings)

    case, agent = _case("u4")
    case.input = {**case.input, "disputed": "waive"}
    run_case(case, agent, Asking(**{f: getattr(real, f) for f in real.__dataclass_fields__}))
    assert "already decided" in case.problems[0]


def test_a_branch_goes_on_where_its_value_says_and_ends_where_told() -> None:
    letters_for_this = [F, C, B({"Treppenhaus 2025": "letters"}, default="end"), L]
    case, agent = _case("u2")
    run = run_case(case, agent, _umlage(procedure=letters_for_this))
    assert run.trace[2] == {"step": "route", "kind": "branch", "to": "letters"}
    assert (run.outcome, len(case.drafts)) == ("ready", 3)

    case, agent = _case("u2")
    run = run_case(case, agent, _umlage(procedure=[F, C, B({"Keller 2025": "letters"}, default="end"), L]))
    assert run.trace[-1] == {"step": "route", "kind": "branch", "to": "end"}
    assert (run.state, run.outcome, case.status, case.drafts) == ("succeeded", "ready", "ready", [])
    assert case.result["title"] == "Treppenhaus 2025"


def test_a_value_with_no_path_fails_the_run_rather_than_guess() -> None:
    case, agent = _case("u2")
    run = run_case(case, agent, _umlage(procedure=[F, C, B({"Keller 2025": "letters"}), L]))
    assert (run.state, run.outcome, case.status) == ("succeeded", "failed", "failed")
    assert case.problems == ["Verzweigung route: kein Weg für den Wert Treppenhaus 2025"]
    assert case.result is None and case.drafts == []
