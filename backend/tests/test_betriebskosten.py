"""The Betriebskostenabrechnung core against the two test cases in docs/use_cases/real_estate/bo.

The expected figures were checked by hand against the documents, not against an expert's answer
key; when one arrives, it replaces them. The invariants (Probe, provenance, nothing guessed) hold
whatever the figures turn out to be.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from app.modules.immobilien.betriebskosten.calculate import (
    NOT_PASSED_ON,
    PARTLY_PASSED_ON,
    _split,
    load_catalogue,
    settle,
)
from app.modules.immobilien.betriebskosten.model import VACANCY, Case, Policy, parse_case
from app.modules.immobilien.betriebskosten.verify import verify

FIXTURES = Path(__file__).parent / "fixtures" / "betriebskosten"
CATALOGUE = load_catalogue()
UNIT_SHARE = Policy(basis="unit_share")


def _case(name: str) -> Case:
    return parse_case(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8")))


def _statement(settlement, party: str):
    return next(s for s in settlement.statements if s.party == party)


def _finding(settlement, position_id: str):
    return next(f for f in settlement.protocol if f.position_id == position_id)


@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_a_cost_split_by_another_key_than_the_lease_is_asked_not_assumed(name) -> None:
    settlement = settle(_case(name), CATALOGUE)

    assert not settlement.ready
    assert "Gesamtabrechnung der WEG" in settlement.questions[0]
    assert settlement.statements == ()  # no figures while a question is open
    assert settlement.proposal is None


@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_both_cases_pass_their_postcondition(name) -> None:
    case = _case(name)
    settlement = settle(case, CATALOGUE, UNIT_SHARE)

    assert settlement.ready
    assert verify(case, settlement) == []
    # Probe, exact to the cent: the parties' shares add up to the allocable costs.
    assert sum(s.total for s in settlement.statements) == settlement.allocable_total


def test_nk01_settlement() -> None:
    settlement = settle(_case("nk01"), CATALOGUE, UNIT_SHARE)

    (tenant,) = settlement.statements
    assert tenant.party == "Familie Brettschneider"
    assert settlement.allocable_total == Decimal("2331.40")
    assert tenant.advances == Decimal("2160.00")
    assert tenant.balance == Decimal("171.40")
    assert settlement.proposal.proposed == Decimal("194.28")
    assert settlement.deadline == date(2026, 12, 31)


def test_nk01_traps() -> None:
    settlement = settle(_case("nk01"), CATALOGUE, UNIT_SHARE)

    # The caretaker's small repairs are held back; the rest of the caretaker is passed on.
    hauswart = _finding(settlement, "U1-10")
    assert (hauswart.treatment, hauswart.amount) == (PARTLY_PASSED_ON, Decimal("78.00"))
    # Cable TV under a bulk contract stopped being allocable after 30 June 2024.
    assert _finding(settlement, "U1-11").treatment == NOT_PASSED_ON
    # Property tax is not in the association's bill but is the tenant's to pay.
    grundsteuer = next(
        line for line in settlement.statements[0].lines if line.position_id == "U2-01"
    )
    assert grundsteuer.share == Decimal("186.40")
    held_back = {f.position_id for f in settlement.protocol if f.treatment == NOT_PASSED_ON}
    assert held_back == {"U1-11", "U1-12", "U1-13", "U1-14", "U1-15"}


def test_nk02_settlement_with_a_change_of_tenant_and_a_vacant_month() -> None:
    settlement = settle(_case("nk02"), CATALOGUE, UNIT_SHARE)

    assert [s.party for s in settlement.statements] == [
        "Herr Özdemir",
        VACANCY,
        "Frau Lindqvist",
    ]
    oezdemir = _statement(settlement, "Herr Özdemir")
    vacancy = _statement(settlement, VACANCY)
    lindqvist = _statement(settlement, "Frau Lindqvist")

    assert settlement.allocable_total == Decimal("1950.30")
    assert (oezdemir.total, oezdemir.advances, oezdemir.balance) == (
        Decimal("1032.61"),
        Decimal("990.00"),
        Decimal("42.61"),
    )
    assert (lindqvist.total, lindqvist.advances, lindqvist.balance) == (
        Decimal("778.61"),
        Decimal("975.00"),
        Decimal("-196.39"),
    )
    assert vacancy.total == Decimal("139.08")  # the landlord's own share
    assert vacancy.advances == Decimal("0.00")
    # The next advance comes from the whole year, not Lindqvist's five autumn months.
    assert settlement.proposal.tenant == "Frau Lindqvist"
    assert settlement.proposal.proposed == Decimal("162.53")


def test_nk02_heating_split() -> None:
    settlement = settle(_case("nk02"), CATALOGUE, UNIT_SHARE)

    heating = {
        s.party: next(line.share for line in s.lines if line.position_id == "U1-02")
        for s in settlement.statements
    }
    # Base 40 % by days (181/31/153); consumption 68 % to the first half by the reading at 30.06.;
    # the remaining 32 % split between July and August–December by degree days.
    assert heating == {
        "Herr Özdemir": Decimal("359.45"),
        VACANCY: Decimal("23.78"),
        "Frau Lindqvist": Decimal("209.57"),
    }
    assert any("Gradtagszahlen" in a for a in settlement.assumptions)


def test_nk02_traps() -> None:
    settlement = settle(_case("nk02"), CATALOGUE, UNIT_SHARE)

    assert _finding(settlement, "U1-12").citation.startswith("BGH")  # renting smoke detectors
    assert _finding(settlement, "U1-17").treatment == NOT_PASSED_ON  # change-of-tenant reading
    assert _finding(settlement, "U1-11").treatment == NOT_PASSED_ON  # maintenance, not agreed
    assert _finding(settlement, "*").citation.startswith("BGH")  # vacancy is the landlord's


def test_smoke_detector_maintenance_is_passed_on_when_the_lease_names_it() -> None:
    case = _case("nk02")
    case = dataclasses.replace(
        case, lease=dataclasses.replace(case.lease, named_other_costs=("rauchwarnmelder_wartung",))
    )
    settlement = settle(case, CATALOGUE, UNIT_SHARE)

    assert all(f.position_id != "U1-11" for f in settlement.protocol)
    assert settlement.allocable_total == Decimal("1968.90")
    assert verify(case, settlement) == []


def test_an_unclassified_position_blocks_the_settlement() -> None:
    case = _case("nk01")
    positions = list(case.positions)
    positions[0] = dataclasses.replace(positions[0], category=None)
    settlement = settle(dataclasses.replace(case, positions=tuple(positions)), CATALOGUE, UNIT_SHARE)

    assert not settlement.ready
    assert "Wasser und Abwasser" in settlement.questions[0]
    assert settlement.statements == ()


def test_a_period_straddling_a_rule_change_is_asked() -> None:
    case = dataclasses.replace(
        _case("nk01"), period_start=date(2024, 1, 1), period_end=date(2024, 12, 31)
    )
    case = dataclasses.replace(
        case,
        tenancies=(
            dataclasses.replace(case.tenancies[0], start=date(2024, 1, 1), end=date(2024, 12, 31)),
        ),
    )
    settlement = settle(case, CATALOGUE, UNIT_SHARE)

    assert any("30.06.2024" in q for q in settlement.questions)


def test_advances_for_a_part_month_must_be_given() -> None:
    case = _case("nk01")
    case = dataclasses.replace(
        case, tenancies=(dataclasses.replace(case.tenancies[0], start=date(2025, 1, 15)),)
    )
    settlement = settle(case, CATALOGUE, UNIT_SHARE)

    assert any("tatsächlich geleisteten Vorauszahlungen" in q for q in settlement.questions)


def test_verify_catches_a_figure_that_does_not_trace_to_the_documents() -> None:
    case = _case("nk02")
    settlement = settle(case, CATALOGUE, UNIT_SHARE)
    first = settlement.statements[0]
    tampered_line = dataclasses.replace(first.lines[0], share=first.lines[0].share + 1)
    tampered = dataclasses.replace(
        settlement,
        statements=(
            dataclasses.replace(first, lines=(tampered_line, *first.lines[1:])),
            *settlement.statements[1:],
        ),
    )

    problems = verify(case, tampered)
    assert any("shares do not add up" in p for p in problems)
    assert any("total is not the sum" in p for p in problems)


@pytest.mark.parametrize(
    ("cents", "weights"),
    [(59280, [181, 31, 153]), (1, [1, 1, 1]), (10000, [1, 1, 1]), (35568, [68, 32])],
)
def test_split_always_adds_up_to_the_whole(cents, weights) -> None:
    parts = _split(cents, [Fraction(w) for w in weights])
    assert sum(parts) == cents
    assert all(p >= 0 for p in parts)
