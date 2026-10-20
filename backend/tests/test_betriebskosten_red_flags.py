"""The Betriebskosten red flags of 19 October 2026 (docs/RED_FLAGS_2026-10-19.md C1, C2, C5–C7).

Each test is an input or a tampered settlement the audit showed getting through: the input is now
refused with something to correct, and the postcondition re-derives what it used to take from the
calculator.
"""

from __future__ import annotations

import copy
import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.modules.immobilien.betriebskosten.calculate import load_catalogue, settle
from app.modules.immobilien.betriebskosten.model import (
    VACANCY,
    CaseError,
    Policy,
    parse_case,
)
from app.modules.immobilien.betriebskosten.verify import verify

CATALOGUE = load_catalogue()
UNIT_SHARE = Policy(basis="unit_share")
NK02 = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "betriebskosten" / "nk02.yaml").read_text("utf-8")
)


def _raw():
    return copy.deepcopy(NK02)


def _excluding(raw):
    return next(p for p in raw["positions"] if p.get("excluded"))


def _refused(raw) -> list[str]:
    with pytest.raises(CaseError) as refused:
        parse_case(raw)
    return refused.value.messages


def test_c1_a_negative_deduction_is_refused() -> None:
    raw = _raw()
    _excluding(raw)["excluded"][0]["amount"] = "-500.00"

    assert any("positiver Betrag" in m for m in _refused(raw))


def test_c1_the_check_refuses_it_even_if_it_got_past_the_input() -> None:
    case = parse_case(_raw())
    position = next(p for p in case.positions if p.excluded)
    bad = dataclasses.replace(
        position, excluded=(dataclasses.replace(position.excluded[0], amount=Decimal("-500.00")),)
    )
    case = dataclasses.replace(
        case, positions=tuple(bad if p.id == position.id else p for p in case.positions)
    )

    problems = verify(case, settle(case, CATALOGUE, UNIT_SHARE), CATALOGUE)

    assert any("outside 0 and the amount" in p or "positive amount" in p for p in problems)


@pytest.mark.parametrize("name", ["same", VACANCY])
def test_c2_a_tenant_must_be_named_once_and_not_like_the_vacancy(name) -> None:
    raw = _raw()
    raw["tenancies"][1]["tenant"] = raw["tenancies"][0]["tenant"] if name == "same" else name

    assert _refused(raw)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["heating"].update(
            readings=[{"end": r["period_end"], "share": "0.5"}, {"end": r["period_end"], "share": "0.5"}]
        ),
        lambda r: r["heating"].update(readings=[{"end": "1999-01-31", "share": "1"}]),
        lambda r: r["positions"][0].update(amount="NaN"),
    ],
    ids=["two-readings-one-day", "reading-outside-the-period", "not-a-number"],
)
def test_c5_input_that_crashed_compute_is_a_correction_to_make(change) -> None:
    raw = _raw()
    change(raw)

    assert _refused(raw)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["positions"][0].update(amount="268.405"),
        lambda r: r["heating"].update(base_share="1.5"),
    ],
    ids=["three-decimals", "base-share-above-half"],
)
def test_c6_implausible_figures_are_refused_not_rounded(change) -> None:
    raw = _raw()
    change(raw)

    assert _refused(raw)


def test_c6_a_half_year_proposes_the_advance_from_six_months() -> None:
    raw = _raw()
    raw["period_end"] = date(raw["period_start"].year, 6, 30)
    raw["tenancies"] = [dict(raw["tenancies"][0], end=raw["period_end"])]
    raw["tenancies"][0].pop("advances_paid", None)
    raw["heating"]["readings"] = [{"end": raw["period_end"], "share": "1"}]
    case = parse_case(raw)

    settlement = settle(case, CATALOGUE, UNIT_SHARE)

    assert settlement.ready, settlement.questions
    expected = (settlement.allocable_total / 6).quantize(Decimal("0.01"))
    assert settlement.proposal.proposed == expected


def _move(settlement, position_id: str, cents: int):
    """Shift `cents` of one position from the first statement to the second, keeping every total
    and balance consistent — a calculator bug that agrees with itself."""
    first, second, *rest = settlement.statements
    amount = Decimal(cents) / 100

    def shifted(statement, delta):
        lines = tuple(
            dataclasses.replace(line, share=line.share + delta)
            if line.position_id == position_id
            else line
            for line in statement.lines
        )
        total = statement.total + delta
        return dataclasses.replace(
            statement, lines=lines, total=total, balance=total - statement.advances
        )

    return dataclasses.replace(
        settlement, statements=(shifted(first, -amount), shifted(second, amount), *rest)
    )


def test_c7_a_share_not_by_days_is_caught_though_everything_adds_up() -> None:
    case = parse_case(_raw())
    settlement = settle(case, CATALOGUE, UNIT_SHARE)
    heating = case.heating.position_id
    position = next(p for p in case.positions if p.id != heating and p.amount > 100)

    problems = verify(case, _move(settlement, position.id, 1000), CATALOGUE)

    assert any("not their share by days" in p for p in problems)


def test_c7_a_cost_the_catalogue_does_not_allow_is_caught() -> None:
    case = parse_case(_raw())
    settlement = settle(case, CATALOGUE, UNIT_SHARE)
    held = next(f for f in settlement.protocol if f.treatment == "nicht umgelegt")
    # The calculator "forgot" the rule: the protocol and every line say it is allocable.
    statements = tuple(
        dataclasses.replace(
            s,
            lines=tuple(
                dataclasses.replace(line, allocable=line.amount)
                if line.position_id == held.position_id
                else line
                for line in s.lines
            ),
        )
        for s in settlement.statements
    )
    tampered = dataclasses.replace(
        settlement,
        statements=statements,
        protocol=tuple(f for f in settlement.protocol if f is not held),
    )

    problems = verify(case, tampered, CATALOGUE)

    assert any("not what the catalogue allows" in p for p in problems)


def test_c7_the_vacancy_is_never_credited_advances() -> None:
    case = parse_case(_raw())
    settlement = settle(case, CATALOGUE, UNIT_SHARE)
    statements = tuple(
        dataclasses.replace(s, advances=Decimal("975.00"), balance=s.total - Decimal("975.00"))
        if s.party == VACANCY
        else s
        for s in settlement.statements
    )

    problems = verify(case, dataclasses.replace(settlement, statements=statements), CATALOGUE)

    assert "the vacancy is credited advances" in problems


def test_both_cases_still_pass_the_stricter_check() -> None:
    for name in ("nk01", "nk02"):
        raw = yaml.safe_load(
            (Path(__file__).parent / "fixtures" / "betriebskosten" / f"{name}.yaml").read_text(
                "utf-8"
            )
        )
        case = parse_case(raw)
        assert verify(case, settle(case, CATALOGUE, UNIT_SHARE), CATALOGUE) == []

