"""The platform's rules for letters (ADR-0034): a letter says no amount the result does not
hold, and a case whose letters break that shows neither the result nor any letter. Run on the
demo definition; the Betriebskosten letters' content is tested with that template.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_tenant
from app.api.v1 import workflow_agents as routes
from app.kernel.drafts import amounts_not_in
from app.kernel.modules import workflow
from app.kernel.workflows import Draft, Workflow
from app.main import app
from tests.test_workflow_agents import _as, _cleanup, _fixture, _PROPERTY, db

SETTINGS = {"default_category": "dienstleistung", "sender": "Hausverwaltung Muster", "payment_days": 30}


def test_an_amount_the_result_does_not_contain_is_found() -> None:
    result = {"statements": [{"balance": "-196.39", "total": "2331.40"}]}
    text = "Guthaben von 196,39 €. Kosten 2.331,40 €. Gebühr 12,00 €. § 560 Abs. 4, 30 Tagen."
    assert amounts_not_in(text, result) == ["12,00 €"]


def test_a_draft_is_a_plain_record() -> None:
    assert dataclasses.asdict(Draft("t", "r", "x")) == {
        "title": "t", "recipient": "r", "text": "x", "enclosure": "", "enclosure_name": "",
        "sender": "", "sender_address": "",
    }


# --- on a case, against the database -------------------------------------------------------

client = TestClient(app)


@pytest.fixture
def property_workspace():
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "demo")
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _ready_case(name: str) -> dict:
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "umlage", "name": "A"}
    ).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture(name)})
    return client.post(f"/api/v1/cases/{case['id']}/compute").json()


@db
def test_a_ready_case_carries_its_letters_and_saving_the_input_clears_them(
    property_workspace,
) -> None:
    case = _ready_case("u2")
    assert case["status"] == "ready"
    assert [d["recipient"] for d in case["drafts"]] == ["Herr Adler", "Frau Berg", "Firma Clausen"]

    edited = client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": _fixture("u2")},
    ).json()
    assert edited["drafts"] == []


@db
def test_a_letter_with_an_amount_not_in_the_result_hides_everything(
    property_workspace, monkeypatch
) -> None:
    module, real = workflow("umlage")

    class Wrong(Workflow):
        def drafts(self, input, settings, result, function="letters"):
            return [Draft("T", "Herr Adler", "Bitte zahlen Sie 999,99 €.")]

    wrong = Wrong(**{f.name: getattr(real, f.name) for f in dataclasses.fields(real)})
    monkeypatch.setattr(routes, "find_workflow", lambda key: (module, wrong))
    case = _ready_case("u2")

    assert case["status"] == "failed"
    assert case["result"] is None and case["drafts"] == []
    assert case["problems"] == ["Schreiben an Herr Adler: 999,99 € steht nicht im Ergebnis"]


def test_an_amount_the_result_prints_in_its_own_text_counts_as_in_the_result() -> None:
    # Found by the Kautionsabrechnung (ADR-0036): a retention's reason quotes its evidence,
    # "Nachzahlung 2024: 380,00 €", and the letter's enclosure repeats that reason.
    result = {"items": [{"reason": "Einbehalt; Nachweis: Nachzahlung 2024: 380,00 €", "amount": "450.00"}]}
    assert amounts_not_in("Grundlage: Nachzahlung 2024: 380,00 €; einbehalten 450,00 €.", result) == []
    assert amounts_not_in("Grundlage: 381,00 €.", result) == ["381,00 €"]
