"""The letters to the tenants (ADR-0032).

Written from the computed result by a template, not a model: one per tenant, none for a vacant
month, at most five sentences, with the balance and the deadline. The kernel refuses any letter
whose euro amounts are not in the result.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.deps import current_tenant
from app.api.v1 import workflow_agents as routes
from app.kernel.drafts import amounts_not_in
from app.kernel.schema import empty
from app.kernel.workflows import Draft
from app.kernel.modules import workflow
from app.main import app
from tests.test_betriebskosten import FIXTURES
from tests.test_workflow_agents import _as, _cleanup, _fixture, _PROPERTY, db

_, BETRIEBSKOSTEN = workflow("betriebskosten")
SETTINGS = {"basis": "unit_share", "sender": "Hausverwaltung Muster", "payment_days": 30}


def _letters(name: str, settings: dict = SETTINGS) -> list[Draft]:
    raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))
    parsed = BETRIEBSKOSTEN.parse(raw, settings)
    computed = BETRIEBSKOSTEN.compute(parsed)
    return BETRIEBSKOSTEN.drafts(parsed, computed.value)


def _sentences(letter: Draft) -> list[str]:
    """The body's sentences, between the salutation and the closing. A sentence starts with a
    capital letter, so "§ 560 Abs. 4" is not two."""
    body = letter.text.split("\n\n")[1]
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", body.strip()) if s]


def test_nk01_one_letter_with_the_balance_the_deadline_and_the_new_advance() -> None:
    (letter,) = _letters("nk01")

    assert letter.recipient == "Familie Brettschneider"
    assert letter.text.startswith("Sehr geehrte Familie Brettschneider,")
    assert "Nachzahlung von 171,40 €" in letter.text
    assert "innerhalb von 30 Tagen" in letter.text
    assert "von 180,00 € auf 194,28 €" in letter.text
    assert letter.text.rstrip().endswith("Hausverwaltung Muster")
    assert len(_sentences(letter)) <= 5


def test_nk02_a_letter_per_tenant_none_for_the_vacant_month() -> None:
    oezdemir, lindqvist = _letters("nk02")

    assert (oezdemir.recipient, lindqvist.recipient) == ("Herr Özdemir", "Frau Lindqvist")
    assert oezdemir.text.startswith("Sehr geehrter Herr Özdemir,")
    assert "Nachzahlung von 42,61 €" in oezdemir.text
    assert "Vorauszahlung" not in oezdemir.text.split("\n\n")[1].split("Daraus")[1]  # no proposal
    assert lindqvist.text.startswith("Sehr geehrte Frau Lindqvist,")
    assert "Guthaben von 196,39 €" in lindqvist.text  # a refund reads as a positive amount
    assert "erstatten" in lindqvist.text
    assert "von 195,00 € auf 162,53 €" in lindqvist.text
    assert all(len(_sentences(letter)) <= 5 for letter in (oezdemir, lindqvist))


def test_every_amount_in_the_letters_is_in_the_result() -> None:
    for name in ("nk01", "nk02"):
        raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))
        parsed = BETRIEBSKOSTEN.parse(raw, SETTINGS)
        computed = BETRIEBSKOSTEN.compute(parsed)
        for letter in BETRIEBSKOSTEN.drafts(parsed, computed.value):
            assert amounts_not_in(letter.text, computed.result) == []


def test_an_amount_the_result_does_not_contain_is_found() -> None:
    result = {"statements": [{"balance": "-196.39", "total": "2331.40"}]}
    text = "Guthaben von 196,39 €. Kosten 2.331,40 €. Gebühr 12,00 €. § 560 Abs. 4, 30 Tagen."
    assert amounts_not_in(text, result) == ["12,00 €"]


def test_a_new_agent_starts_with_the_settings_defaults() -> None:
    settings = empty(BETRIEBSKOSTEN.schemas()["settings"])
    assert settings == {"basis": None, "sender": "", "payment_days": 30}


def test_an_agent_from_before_the_letter_settings_still_computes() -> None:
    (letter,) = _letters("nk01", {"basis": "unit_share"})
    assert "innerhalb von 30 Tagen" in letter.text
    assert "Mit freundlichen Grüßen" in letter.text


def test_a_draft_is_a_plain_record() -> None:
    assert dataclasses.asdict(Draft("t", "r", "x")) == {
        "title": "t", "recipient": "r", "text": "x", "enclosure": "", "enclosure_name": "",
    }


# --- on a case, against the database -------------------------------------------------------

client = TestClient(app)


@pytest.fixture
def property_workspace():
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "immobilien")
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _ready_case(name: str) -> dict:
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "betriebskosten", "name": "A"}
    ).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture(name)})
    return client.post(f"/api/v1/cases/{case['id']}/compute").json()


@db
def test_a_ready_case_carries_its_letters_and_saving_the_input_clears_them(
    property_workspace,
) -> None:
    case = _ready_case("nk02")
    assert case["status"] == "ready"
    assert [d["recipient"] for d in case["drafts"]] == ["Herr Özdemir", "Frau Lindqvist"]

    edited = client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": _fixture("nk02")},
    ).json()
    assert edited["drafts"] == []


@db
def test_a_letter_with_an_amount_not_in_the_result_hides_everything(
    property_workspace, monkeypatch
) -> None:
    module, real = workflow("betriebskosten")

    def wrong(parsed, value):
        return [Draft("T", "Herr Özdemir", "Bitte zahlen Sie 999,99 €.")]

    monkeypatch.setattr(
        routes, "find_workflow", lambda key: (module, dataclasses.replace(real, drafts=wrong))
    )
    case = _ready_case("nk02")

    assert case["status"] == "failed"
    assert case["result"] is None and case["drafts"] == []
    assert case["problems"] == ["Schreiben an Herr Özdemir: 999,99 € steht nicht im Ergebnis"]


def test_each_letter_carries_the_statement_it_says_is_enclosed() -> None:
    oezdemir, lindqvist = _letters("nk02")

    assert oezdemir.enclosure_name == "Einzelaufstellung Herr Özdemir.txt"
    assert "Herr Özdemir" in oezdemir.enclosure
    assert "01.01.2025 – 30.06.2025" in oezdemir.enclosure
    assert "Heizung und Warmwasser" in oezdemir.enclosure and "359,45 €" in oezdemir.enclosure
    assert "Saldo (Nachzahlung)" in oezdemir.enclosure and "42,61 €" in oezdemir.enclosure
    assert "Miete der Rauchwarnmelder" in oezdemir.enclosure  # what was not passed on, and why
    assert "BGH, Urt. v. 11.05.2022" in oezdemir.enclosure
    assert "Saldo (Guthaben)" in lindqvist.enclosure and "196,39 €" in lindqvist.enclosure


def test_the_enclosures_amounts_are_in_the_result_too() -> None:
    raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / "nk02.yaml").read_text()), default=str))
    parsed = BETRIEBSKOSTEN.parse(raw, SETTINGS)
    computed = BETRIEBSKOSTEN.compute(parsed)
    for letter in BETRIEBSKOSTEN.drafts(parsed, computed.value):
        assert amounts_not_in(letter.enclosure, computed.result) == []
