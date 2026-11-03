"""The publish gate (ADR-0039 step 4): a version is offered only if every one of its cases passes.

The cases are data in the definition (`cases/*.yaml`), run the way a real case runs: through the
procedure, each function in the sandbox, with the platform's check and letter check. Every case
names its source and every expected figure where it was copied from (ADR-0038 D4); a definition
without cases, or without a counter-check, is not published.
"""

from __future__ import annotations

import copy

import pytest

from app.definitions import service as definitions
from app.definitions.gate import GateRefused, guardrails, require, run_gate
from app.kernel.definitions import DefinitionError, bundles_dir, check_content, checksum, read_definition, workflow_from
from tests.test_definition_versions import DEMO, _demo_content, _in_db, seeded  # noqa: F401
from tests.test_workflow_agents import db, property_workspace  # noqa: F401


def _content(**changes) -> dict:
    return {**copy.deepcopy(read_definition(DEMO / "umlage", DEMO)), **changes}


def _case(content: dict, name: str) -> dict:
    return next(c for c in content["tests"] if c["name"] == name)


def _gate(content: dict) -> dict:
    return run_gate(workflow_from(content, bundles_dir()), content)


def test_the_demo_passes_every_case_it_names() -> None:
    report = _gate(_content())
    assert report["passed"] and "refused" not in report
    assert [(c["name"], c["passed"]) for c in report["cases"]] == [("u2", True), ("u3", True), ("u4", True)]
    assert all(c["source"] for c in report["cases"])


def test_a_wrong_figure_fails_and_says_which_and_where_it_was_copied_from() -> None:
    content = _content()
    _case(content, "u2")["expect"]["result"]["statements[party=Herr Adler].balance"]["value"] = "58.48"
    report = _gate(content)

    assert not report["passed"]
    (u2,) = [c for c in report["cases"] if not c["passed"]]
    assert u2["failures"] == ["statements[party=Herr Adler].balance: 58.47, expected 58.48 (1.048,47 − 990,00)"]
    with pytest.raises(GateRefused, match="u2: statements"):
        require(workflow_from(content, bundles_dir()), content)


def test_a_counter_check_catches_what_the_figures_would_not() -> None:
    content = _content()
    _case(content, "u2")["counter"].append({"no_letter_matches": "Guten Tag", "why": "a test hedge"})
    failures = next(c for c in _gate(content)["cases"] if c["name"] == "u2")["failures"]
    assert failures == [
        "a test hedge: the letter to Herr Adler says 'Guten Tag'",
        "a test hedge: the letter to Frau Berg says 'Guten Tag'",
        "a test hedge: the letter to Firma Clausen says 'Guten Tag'",
    ]


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        (lambda c: c["expect"].update(status="ready"), "status waiting, expected ready"),
        (lambda c: c["expect"].update(waiting_for=["payments.1.amount"]), "waiting_for ['payments.2.amount']"),
        (lambda c: c["expect"].update(letters=["Herr Adler"]), "letters to ['Herr Adler', 'Frau Berg']"),
        (lambda c: c["expect"].update(result={"nothing.here": {"value": "1", "ref": "x"}}), "nothing.here: not in the result"),
    ],
)
def test_a_case_that_comes_out_otherwise_fails(change, failure) -> None:
    content = _content()
    change(_case(content, "u3"))
    failures = next(c for c in _gate(content)["cases"] if c["name"] == "u3")["failures"]
    assert any(failure in f for f in failures), failures


def test_without_cases_or_without_a_counter_check_nothing_is_published() -> None:
    assert "no test cases" in _gate(_content(tests=[]))["refused"]
    no_counters = _content()
    for case in no_counters["tests"]:
        case.pop("counter", None)
    assert "counter-check" in _gate(no_counters)["refused"]


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (lambda c: c.pop("source"), "names no source"),
        (lambda c: c["expect"]["result"]["total"].pop("ref"), "where it was copied from"),
        (lambda c: c["expect"].update(status="fine"), "expects a status"),
        (lambda c: c["expect"].update(mood="good"), "expects a status"),
        (lambda c: c["counter"].append({"no_letter_matches": "x"}), "with why"),
        (lambda c: c["counter"].append({"no_letter_matches": "(", "why": "x"}), "is not a pattern"),
        (lambda c: c["expect"]["result"].update({"a[b": {"value": "1", "ref": "x"}}), "not a path"),
    ],
)
def test_a_case_without_a_source_a_ref_or_a_readable_check_is_refused(change, refused) -> None:
    content = _content()
    change(_case(content, "u2"))
    with pytest.raises(DefinitionError, match=refused):
        check_content(content)


def test_a_case_may_not_read_its_input_from_outside_its_definition(tmp_path) -> None:
    import shutil

    module = tmp_path / "demo"
    shutil.copytree(DEMO, module)
    (module / "umlage" / "cases" / "outside.yaml").write_text(
        "source: x\ninput_file: ../../secret.yaml\nexpect: {status: ready}\n", encoding="utf-8"
    )
    (tmp_path / "secret.yaml").write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(DefinitionError, match="outside the definition"):
        read_definition(module / "umlage", module)


def test_the_cases_are_part_of_the_version_and_are_exported_with_it(tmp_path) -> None:
    content = _content()
    directory = definitions.export_files(content, tmp_path)
    again = read_definition(directory, directory.parent)
    assert again["tests"] == content["tests"] and checksum(again) == checksum(content)


def test_the_guardrail_panel_states_the_fixed_answers() -> None:
    content = _content()
    workflow = workflow_from(content, bundles_dir(), gate=_gate(content))
    panel = {row["question"]: row for row in guardrails(workflow)}

    assert not any(row["can_switch_off"] for row in panel.values())
    assert panel["Is every function's result checked?"]["answer"].startswith("Yes: a check runs after compute")
    assert "3 cases (u2, u3, u4) with 2 counter-checks, all passed" in panel["Which tests must pass before publishing?"]["answer"]
    assert panel["Which judgments are a person's?"]["answer"].startswith("disputed:")
    files = guardrails(workflow_from(content, bundles_dir()))
    assert "Not run" in next(r for r in files if r["question"].startswith("Which tests"))["answer"]


# --- publishing, against the database --------------------------------------------------------


@db
def test_a_draft_whose_case_fails_is_not_published_and_nothing_changes(seeded) -> None:  # noqa: F811
    content = _demo_content("umlage_gate")
    _case(content, "u2")["expect"]["result"]["total"]["value"] = "1.00"

    async def go(session):
        await definitions.save_draft(session, "umlage_gate", "demo", content)
        with pytest.raises(GateRefused, match="u2: total: 1940.90, expected 1.00"):
            await definitions.publish(session, "umlage_gate")
        await session.rollback()
        definition = await definitions.repo.get_definition(session, "umlage_gate")
        return (await definitions.repo.definition_draft(session, definition.id)).status

    assert _in_db(go) == "draft"


@db
def test_a_published_version_keeps_the_gate_report_it_passed(seeded) -> None:  # noqa: F811
    async def go(session):
        await definitions.save_draft(session, "umlage_gate_ok", "demo", _demo_content("umlage_gate_ok"))
        version = await definitions.publish(session, "umlage_gate_ok")
        return version.status, version.gate

    status, gate = _in_db(go)
    assert status == "published" and gate["passed"]
    assert [c["name"] for c in gate["cases"]] == ["u2", "u3", "u4"]


@db
def test_an_agent_shows_the_guardrails_of_the_version_it_runs(property_workspace) -> None:  # noqa: F811
    from tests.test_workflow_agents import _agent, client

    detail = client.get(f"/api/v1/workflow-agents/{_agent()['id']}").json()
    questions = [g["question"] for g in detail["guardrails"]]
    assert "Which tests must pass before publishing?" in questions
    assert all(g["can_switch_off"] is False for g in detail["guardrails"])
