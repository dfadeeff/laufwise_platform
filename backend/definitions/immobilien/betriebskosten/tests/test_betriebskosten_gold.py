"""NK-01 and NK-02 against the expert's own answers (ADR-0038 D4).

The expected figures live in `fixtures/nk0N.expected.yaml`, each copied from the
expert's sheet with its place in it, never from the platform's output. This file only compares.

Next to the figures, the counter-checks of the expert's review (Lessons Learned NK 01/02, § 6):
an answer is wrong when it invents a figure, and also when it holds back one that a norm
settles. A result marked conditional, an open item without an addressee and a date, or a letter
without a deadline fails here as surely as a wrong amount.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from betriebskosten.calculate import settle
from betriebskosten.host import load_catalogue
from betriebskosten.verify import verify
from betriebskosten.workflow import _drafts, _parse

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGUE = load_catalogue()
SETTINGS = {"basis": "unit_share", "sender": "Hausverwaltung Muster", "payment_days": 30}

# Words that mark a figure as not meant (the agent's NK-02 letters): a tenant cannot pay those.
_HEDGES = re.compile(r"Annahme|bedingt|Szenario|BLOCKED|nicht festgesetzt|nicht abschließend", re.I)
# A deadline in a letter: a date, or a period in days from receipt.
_DEADLINE = re.compile(r"\d{2}\.\d{2}\.\d{4}|innerhalb von \d+ Tagen")


def _raw(name: str) -> dict:
    return json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))


def _run(name: str):
    """In-process, with the definition's own code: the figures field by field."""
    parsed = _parse(_raw(name), SETTINGS)
    return parsed, settle(parsed.case, CATALOGUE, parsed.policy)


def _expected(name: str) -> dict:
    return yaml.safe_load((FIXTURES / f"{name}.expected.yaml").read_text(encoding="utf-8"))


def _close(actual: Decimal, figure: dict, tolerance: Decimal) -> bool:
    """Within the file's tolerance, or the figure's own where its sheet says why."""
    return abs(actual - Decimal(figure["value"])) <= Decimal(figure.get("tolerance", tolerance))


def _heating(statement, heating_id: str) -> Decimal:
    return next(line.share for line in statement.lines if line.position_id == heating_id)


@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_the_figures_are_the_experts(name) -> None:
    expected = _expected(name)
    tolerance = Decimal(expected["tolerance"])
    parsed, computed = _run(name)
    s = computed

    assert computed.questions == ()
    assert verify(parsed.case, s, CATALOGUE) == []
    wrong = []
    if s.allocable_total != Decimal(expected["allocable_total"]["value"]):
        wrong.append(("allocable_total", s.allocable_total, expected["allocable_total"]["ref"]))
    if s.deadline != expected["deadline"]["value"]:
        wrong.append(("deadline", s.deadline, expected["deadline"]["ref"]))

    lines = {line.position_id: line for line in s.statements[0].lines}
    for pid, figure in expected["allocable"].items():
        if lines[pid].allocable != Decimal(figure["value"]):
            wrong.append((f"{pid} allocable", lines[pid].allocable, figure["ref"]))

    statements = {st.party: st for st in s.statements}
    for party, want in expected["statements"].items():
        st = statements[party]
        if st.final != want["final"]:
            wrong.append((f"{party} final", st.final, "status"))
        heating = _heating(st, parsed.case.heating.position_id)
        actual = {
            "total": st.total,
            "advances": st.advances,
            "balance": st.balance,
            "heating": heating,
            "non_heating": st.total - heating,
        }
        for key in ("total", "advances", "balance", "heating", "non_heating"):
            if key in want and (actual[key] is None or not _close(actual[key], want[key], tolerance)):
                wrong.append((f"{party} {key}", actual[key], want[key]["ref"]))

    if "proposal" in expected:
        want = expected["proposal"]
        if s.proposal is None or s.proposal.tenant != want["tenant"] or s.proposal.proposed != Decimal(want["proposed"]["value"]):
            wrong.append(("proposal", s.proposal and s.proposal.proposed, want["proposed"]["ref"]))
    assert wrong == []


def test_nk02_the_july_boundary_is_one_open_position_with_a_method_an_addressee_and_a_date() -> None:
    (want,) = _expected("nk02")["open_items"]
    _, computed = _run("nk02")
    s = computed

    (item,) = s.open_items
    assert item.position_id == want["position_id"]
    assert item.amount == Decimal(want["amount"]["value"])
    assert item.addressee == want["addressee"]
    assert want["asks_for"] in item.what
    assert want["method_cites"] in item.method
    assert item.due <= want["due_on_or_before"]
    # Open is one position for the two parties sharing it, not a blocked settlement.
    assert {st.party for st in s.statements if not st.final} == {"Vermieter (Leerstand)", "Frau Lindqvist"}
    assert all(st.balance is None for st in s.statements if not st.final)


@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_counter_checks_nothing_is_held_back_or_hedged(name) -> None:
    parsed, computed = _run(name)
    s = computed
    letters = _drafts(parsed, s)
    tenants = {st.party for st in s.statements if st.party != "Vermieter (Leerstand)"}

    # NO_ANSWER_WHERE_RULE_EXISTS / CONDITIONAL_WHERE_DEFINITIVE: no figure is called an assumption
    # where a norm decides it (the 6/12 Grundkosten are § 9b Abs. 2, not an assumption).
    assert not any("Grundkosten" in a for a in s.assumptions)
    # MISSING_DEADLINE: the § 556 Abs. 3 date is in every statement sent to a tenant.
    tenant_letters = [letter for letter in letters if letter.recipient in tenants]
    assert {letter.recipient for letter in tenant_letters} == tenants
    for letter in tenant_letters:
        # LETTER_NOT_DISPATCHABLE: a deadline, no hedge, at most five sentences.
        assert _DEADLINE.search(letter.text), letter.recipient
        assert not _HEDGES.search(letter.text + letter.enclosure), letter.recipient
        assert "§ 556 Abs. 3 BGB" in letter.enclosure and f"{s.deadline:%d.%m.%Y}" in letter.enclosure
    for item in s.open_items:
        request = next(letter for letter in letters if letter.recipient == item.addressee)
        assert f"{item.due:%d.%m.%Y}" in request.text


@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_the_platform_runs_it_in_the_sandbox_with_the_experts_balances(name) -> None:
    """The same case through the platform: the definition's functions, each in a sandbox call."""
    from app.kernel.modules import workflow

    _, betriebskosten = workflow("betriebskosten")
    computed = betriebskosten.compute(_raw(name), SETTINGS)
    assert computed.errors == () and computed.questions == ()
    assert betriebskosten.check(_raw(name), SETTINGS, computed.result) == []
    balances = {st["party"]: st["balance"] for st in computed.result["statements"]}
    for party, want in _expected(name)["statements"].items():
        if "balance" in want:
            assert Decimal(balances[party]) == Decimal(want["balance"]["value"]), want["balance"]["ref"]
