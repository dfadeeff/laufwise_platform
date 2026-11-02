"""The Betriebskostenabrechnung core against the two test cases in docs/use_cases/real_estate/bo.

The expert's figures are checked in `test_betriebskosten_gold.py`, from the expert's own sheets.
This file checks the mechanics: the Probe, provenance, nothing guessed, and the traps.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from betriebskosten.calculate import (
    NOT_PASSED_ON,
    PARTLY_PASSED_ON,
    _split,
    settle,
)
from betriebskosten.host import load_catalogue
from betriebskosten.model import VACANCY, Case, Policy, parse_case
from betriebskosten.verify import verify

FIXTURES = Path(__file__).parent / "fixtures"
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
    assert verify(case, settlement, CATALOGUE) == []
    # Probe, exact to the cent: the parties' shares and what is still open add up to the
    # allocable costs.
    shared = sum(s.total for s in settlement.statements) + sum(o.amount for o in settlement.open_items)
    assert shared == settlement.allocable_total


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

    # The first tenant is settled; the vacancy and the new tenant share the open heating pool.
    assert (oezdemir.final, vacancy.final, lindqvist.final) == (True, False, False)
    assert oezdemir.balance == Decimal("58.47")
    assert vacancy.balance is None and lindqvist.balance is None
    assert vacancy.advances == Decimal("0.00")
    # The next advance comes from the whole year of the flat, which is known although one
    # split is open; not Lindqvist's five autumn months.
    assert settlement.proposal.tenant == "Frau Lindqvist"
    assert settlement.proposal.proposed == Decimal("164.08")  # 1.968,90 / 12


def test_nk02_heating_split() -> None:
    settlement = settle(_case("nk02"), CATALOGUE, UNIT_SHARE)

    heating = {
        s.party: next(line.share for line in s.lines if line.position_id == "U1-02")
        for s in settlement.statements
    }
    # Base 40 % by months (6/1/5, § 9b Abs. 2); consumption 68 % to the first half by the reading
    # at 30.06. The remaining 32 % (113,82 €) is open: July and August–December share it, and
    # heating and hot water are split differently there (§ 9b Abs. 3).
    assert heating == {
        "Herr Özdemir": Decimal("360.42"),  # 118,56 + 241,86
        VACANCY: Decimal("19.76"),  # 237,12 × 1/12, consumption open
        "Frau Lindqvist": Decimal("98.80"),  # 237,12 × 5/12, consumption open
    }
    (item,) = settlement.open_items
    assert (item.amount, item.start, item.end) == (Decimal("113.82"), date(2025, 7, 1), date(2025, 12, 31))
    assert not settlement.assumptions or not any("Gradtagszahlen" in a for a in settlement.assumptions)


def test_nk02_once_the_messdienst_states_the_hot_water_share_nothing_is_open() -> None:
    case = _case("nk02")
    heating = dataclasses.replace(case.heating, hot_water_share=Decimal("0.25"))
    settlement = settle(dataclasses.replace(case, heating=heating), CATALOGUE, UNIT_SHARE)

    shares = {
        s.party: next(line.share for line in s.lines if line.position_id == "U1-02")
        for s in settlement.statements
    }
    # By hand, from § 9b Abs. 2: of 113,82 €, hot water 28,46 € by months (1/6 July, 5/6 after)
    # = 4,74 / 23,72; heating 85,36 € by degree days (July 40/3 of 1250/3) = 2,73 / 82,63.
    assert shares == {
        "Herr Özdemir": Decimal("360.42"),
        VACANCY: Decimal("27.23"),  # 19,76 + 4,74 + 2,73
        "Frau Lindqvist": Decimal("205.15"),  # 98,80 + 23,72 + 82,63
    }
    assert settlement.open_items == ()
    assert all(s.final for s in settlement.statements)
    assert verify(dataclasses.replace(case, heating=heating), settlement, CATALOGUE) == []


def test_nk02_traps() -> None:
    settlement = settle(_case("nk02"), CATALOGUE, UNIT_SHARE)

    assert _finding(settlement, "U1-12").citation.startswith("BGH")  # renting smoke detectors
    assert _finding(settlement, "U1-17").treatment == NOT_PASSED_ON  # change-of-tenant reading
    # Maintaining the smoke detectors is passed on, renting them is not (the expert's decision).
    assert all(f.position_id != "U1-11" for f in settlement.protocol)
    assert _finding(settlement, "*").citation.startswith("BGH")  # vacancy is the landlord's


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

    problems = verify(case, tampered, CATALOGUE)
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


def test_a_tenancy_is_held_as_the_documents_state_it_and_clipped_to_the_period() -> None:
    # NK-01 states "Mietbeginn 01.03.2021" and that the tenancy ran through 2025: the documents'
    # dates, not the part inside the period, which the calculator works out itself.
    case = _case("nk01")
    stated = dataclasses.replace(case.tenancies[0], start=date(2021, 3, 1), end=None)
    settlement = settle(dataclasses.replace(case, tenancies=(stated,)), CATALOGUE, UNIT_SHARE)

    (tenant,) = settlement.statements
    assert (tenant.start, tenant.end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert tenant.advances == Decimal("2160.00")
    assert tenant.balance == Decimal("171.40")
    assert settlement.proposal.tenant == "Familie Brettschneider"


def test_an_unstated_start_is_taken_as_the_period_start_and_said_so() -> None:
    case = _case("nk02")
    first = dataclasses.replace(case.tenancies[0], start=None)
    settlement = settle(
        dataclasses.replace(case, tenancies=(first, case.tenancies[1])), CATALOGUE, UNIT_SHARE
    )

    assert _statement(settlement, "Herr Özdemir").balance == Decimal("58.47")
    assert any("Herr Özdemir" in a and "Beginn" in a for a in settlement.assumptions)


def test_a_tenancy_entirely_outside_the_period_is_asked_about() -> None:
    case = _case("nk01")
    old = dataclasses.replace(case.tenancies[0], start=date(2019, 1, 1), end=date(2020, 12, 31))
    settlement = settle(dataclasses.replace(case, tenancies=(old,)), CATALOGUE, UNIT_SHARE)

    assert any("nicht innerhalb des Abrechnungszeitraums" in q for q in settlement.questions)
