"""The Kautionsabrechnung, the second workflow of `immobilien` (ADR-0036).

It is built from the same kernel as the Betriebskostenabrechnung with no kernel change: its own
dataclasses, catalogue, compute and check, letter and reading guide. These tests hold the
domain; the case, the forms, the letter check and the approval gate are the kernel's.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.kernel.drafts import amounts_not_in
from app.kernel.modules import installed, workflow
from app.kernel.workflows import InputError
from app.main import app
from tests.test_approvals import BOB, _as_person, outbox  # noqa: F401 (fixture)
from tests.test_workflow_agents import db

FIXTURES = Path(__file__).parent / "fixtures" / "kaution"
_, KAUTION = workflow("kaution")
SETTINGS = {"sender": "Hausverwaltung Muster GmbH", "payment_days": 14}


def _raw(name: str) -> dict:
    return json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))


def _run(raw: dict, settings: dict = SETTINGS):
    parsed = KAUTION.parse(raw, settings)
    computed = KAUTION.compute(parsed)
    problems = KAUTION.check(parsed, computed.value) if not computed.questions else []
    return parsed, computed, problems


def _item(computed, claim_id: str) -> dict:
    return next(i for i in computed.result["items"] if i["claim_id"] == claim_id)


def test_it_is_a_second_workflow_of_the_same_module() -> None:
    assert [w.key for w in installed()["immobilien"].workflows] == ["betriebskosten", "kaution"]


def test_ka01_refund_with_interest_less_the_unpaid_rent() -> None:
    _, computed, problems = _run(_raw("ka01"))

    assert problems == []
    r = computed.result
    assert (r["paid"], r["interest"], r["credit"]) == ("2220.00", "18.46", "2238.46")
    assert (r["offset"], r["retained"]) == ("740.00", "0.00")
    assert (r["payout"], r["remaining_claim"]) == ("1498.46", "0.00")
    assert _item(computed, "F-01")["treatment"] == "verrechnet"
    assert r["deadline"] == "2026-03-30"  # orientation: six months after the return


def test_ka02_only_what_may_be_charged_is_charged() -> None:
    _, computed, problems = _run(_raw("ka02"))

    assert problems == []
    r = computed.result
    assert r["credit"] == "1823.63"  # 1800 + 32.10 interest − 8.47 tax withheld
    assert (r["offset"], r["retained"], r["payout"]) == ("640.00", "450.00", "733.63")
    assert _item(computed, "F-01")["treatment"] == "verrechnet"  # documented damage
    assert _item(computed, "F-02")["treatment"] == "nicht verrechnet"  # normal wear
    assert _item(computed, "F-02")["citation"] == "§ 538 BGB"
    assert _item(computed, "F-03")["treatment"] == "nicht verrechnet"  # handling fee
    assert _item(computed, "F-04")["treatment"] == "nicht verrechnet"  # invalid clause
    assert "VIII ZR 185/14" in _item(computed, "F-04")["citation"]
    assert _item(computed, "F-05")["treatment"] == "einbehalten"
    assert any("§ 548" in a for a in computed.assumptions)  # limitation stated, not hidden


def test_claims_beyond_the_deposit_leave_a_claim_and_pay_nothing() -> None:
    raw = _raw("ka01")
    raw["claims"].append(
        {"id": "F-02", "label": "Miete August 2025", "amount": "1600.00",
         "category": "mietrueckstand", "source": "Mieterkonto", "evidence": "offen"}
    )
    _, computed, problems = _run(raw)

    assert problems == []
    assert (computed.result["payout"], computed.result["remaining_claim"]) == ("0.00", "101.54")


@pytest.mark.parametrize(
    ("change", "asked"),
    [
        (lambda r: r["claims"][0].update(evidence=""), "Nachweis"),  # damage without evidence
        (lambda r: r["claims"][4].update(evidence=""), "Nachweis"),  # retention without basis
        (lambda r: r.update(renovation_clause=None), "Klausel"),  # clause not assessed
        (lambda r: r["claims"][0].update(category=None), "keiner bekannten Art"),
        (lambda r: r.update(returned_on=None), "Rückgabe"),
        (lambda r: r.update(payments=[]), "keine Kautionszahlung"),
    ],
)
def test_what_the_documents_leave_open_is_asked_and_blocks_the_figures(change, asked) -> None:
    raw = _raw("ka02")
    change(raw)
    _, computed, _ = _run(raw)

    assert computed.result is None
    assert any(asked in q for q in computed.questions)


def test_less_paid_than_agreed_settles_what_was_paid_and_says_so() -> None:
    raw = _raw("ka01")
    raw["payments"] = raw["payments"][:2]
    _, computed, problems = _run(raw)

    assert problems == [] and computed.result["paid"] == "1480.00"
    assert any("1.480,00 €" in a and "2.220,00 €" in a for a in computed.assumptions)


def test_a_deposit_above_three_rents_is_pointed_out() -> None:
    raw = _raw("ka01")
    raw["monthly_rent_net"] = "500.00"
    _, computed, _ = _run(raw)
    assert any("§ 551" in a for a in computed.assumptions)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["claims"][0].update(amount="-740.00"),
        lambda r: r["payments"][0].update(amount="0.00"),
        lambda r: r.update(interest="-1.00"),
        lambda r: r["claims"].append(dict(r["claims"][0])),  # the same claim id twice
    ],
)
def test_impossible_input_is_refused_at_parse(change) -> None:
    raw = _raw("ka01")
    change(raw)
    with pytest.raises(InputError):
        KAUTION.parse(raw, SETTINGS)


def test_the_check_catches_a_payout_that_does_not_follow(monkeypatch) -> None:
    parsed, computed, _ = _run(_raw("ka01"))
    tampered = dataclasses.replace(computed.value, payout=computed.value.payout + Decimal("1"))
    assert KAUTION.check(parsed, tampered)


def test_the_check_catches_a_charge_the_catalogue_does_not_allow() -> None:
    parsed, computed, _ = _run(_raw("ka02"))
    items = list(computed.value.items)
    wear = next(i for i in items if i.claim_id == "F-02")
    items[items.index(wear)] = dataclasses.replace(wear, treatment="verrechnet")
    tampered = dataclasses.replace(
        computed.value,
        items=tuple(items),
        offset=computed.value.offset + wear.amount,
        payout=computed.value.payout - wear.amount,
    )
    assert any("Abnutzung" in p or "F-02" in p for p in KAUTION.check(parsed, tampered))


def test_one_letter_to_the_tenant_every_amount_from_the_result() -> None:
    for name, expected in (("ka01", "1.498,46 €"), ("ka02", "733,63 €")):
        parsed, computed, _ = _run(_raw(name))
        (letter,) = KAUTION.drafts(parsed, computed.value)
        assert expected in letter.text and "innerhalb von 14 Tagen" in letter.text
        assert letter.text.rstrip().endswith("Hausverwaltung Muster GmbH")
        assert amounts_not_in(letter.text + "\n" + letter.enclosure, computed.result) == []
    (letter,) = KAUTION.drafts(*_run(_raw("ka02"))[:1], _run(_raw("ka02"))[1].value)
    assert letter.text.startswith("Sehr geehrte Frau Albers,")
    assert "450,00 €" in letter.text and "Betriebskosten" in letter.text  # the retention, named
    assert "§ 538 BGB" in letter.enclosure  # what was not charged, and why


def test_a_remaining_claim_is_asked_for_in_the_letter() -> None:
    raw = _raw("ka01")
    raw["claims"].append(
        {"id": "F-02", "label": "Miete August 2025", "amount": "1600.00",
         "category": "mietrueckstand", "source": "Mieterkonto", "evidence": "offen"}
    )
    parsed, computed, _ = _run(raw)
    (letter,) = KAUTION.drafts(parsed, computed.value)
    assert "101,54 €" in letter.text and "überweisen" in letter.text


def test_the_reading_guide_leaves_legal_judgment_to_a_person() -> None:
    assert "renovation_clause" in KAUTION.extraction_guide
    assert "null" in KAUTION.extraction_guide


# --- through the kernel, unchanged: the same API, case, letter check and approval gate ----

client = TestClient(app)


@db
def test_a_kaution_case_runs_through_the_kernel_from_input_to_sent_letter(outbox) -> None:  # noqa: F811
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "kaution", "name": "Kautionen"}
    ).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "Albers"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _raw("ka02")})
    ready = client.post(f"/api/v1/cases/{case['id']}/compute").json()

    assert ready["status"] == "ready" and ready["result"]["payout"] == "733.63"
    assert ready["rules_version"] == "kaution catalogue v1"
    assert [d["recipient"] for d in ready["drafts"]] == ["Frau Albers"]

    approval_id = client.post(
        f"/api/v1/cases/{case['id']}/approval", json={"emails": {"Frau Albers": "albers@example.org"}}
    ).json()["approval"]["id"]
    _as_person(BOB)
    sent = client.post(f"/api/v1/approvals/{approval_id}/decide", json={"approve": True}).json()

    assert [d["status"] for d in sent["approval"]["deliveries"]] == ["sent"]
    (mail,) = outbox
    assert mail["Subject"] == "Kautionsabrechnung – Lindenstraße 14, Wohnung Nr. 4"
    assert "733,63 €" in mail.get_body().get_content()
