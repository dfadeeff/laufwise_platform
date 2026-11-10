"""A rent increase under § 558 BGB beside the settlement (ADR-0042 step 5b): the assessment says
which cap applies, the code computes the rest, and the check re-derives it. Made-up figures on
NK-01's flat, not an expert's case."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from app.kernel.drafts import amounts_not_in
from betriebskosten.calculate import settle
from betriebskosten.verify import verify
import pytest

from betriebskosten.workflow import InputError, _drafts, _parse
from sdk import plain
from test_betriebskosten_gold import CATALOGUE, SETTINGS, _raw
from test_betriebskosten_lease import _assessed

RENTS = [
    {"start": "2018-05-01", "rent": "500.00", "surcharge": "0.00", "basis": "vertrag"},
    {"start": "2024-01-01", "rent": "540.00", "surcharge": "0.00", "basis": "558"},
    {"start": "2025-09-01", "rent": "540.00", "surcharge": "30.00", "basis": "559"},
]


def _case(cap: str | None = "15", rents=RENTS, local: str = "12.00", delivered: str = "2026-03-10") -> dict:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    raw = _assessed(raw, every, kind="reference_to_betrkv")
    raw["today"] = "2026-03-01"
    raw["rent_increase"] = {"tenant": "Familie Brettschneider", "area_sqm": "50", "local_rent_per_sqm": local,
                            "delivered": delivered, "rents": rents}
    if cap is not None:
        raw["assessment"]["rent_increase"] = {"cap_percent": cap, "basis": {"source": "Verordnung", "quote": "15 Prozent"}}
    return raw


def _settled(raw: dict):
    parsed = _parse(raw, SETTINGS)
    return parsed, settle(parsed.case, CATALOGUE, parsed.policy)


def test_the_cap_binds_below_the_local_rent_and_the_surcharge_stays_on_top() -> None:
    parsed, s = _settled(_case())
    r = s.rent_increase
    assert (r.effective, r.consent_by, r.base_date) == (date(2026, 6, 1), date(2026, 5, 31), date(2023, 6, 1))
    assert (r.base_rent, r.cap_limit, r.local_rent) == (Decimal("500.00"), Decimal("575.00"), Decimal("600.00"))
    assert (r.binding, r.current_rent, r.new_rent, r.increase) == ("Kappungsgrenze", Decimal("540.00"), Decimal("575.00"), Decimal("35.00"))
    assert (r.surcharge, r.current_total, r.new_total) == (Decimal("30.00"), Decimal("570.00"), Decimal("605.00"))
    assert (r.unchanged_since, r.months_unchanged) == (date(2024, 1, 1), 29)  # the § 559 surcharge does not count
    assert s.deadline_days_left == (s.deadline - date(2026, 3, 1)).days
    assert "verschiedenen Vorschriften" in s.letter_strategy
    assert verify(parsed.case, s, CATALOGUE) == []


def test_a_wider_cap_lets_the_local_rent_bind() -> None:
    _, s = _settled(_case(cap="20", local="11.00"))  # cap 600.00, local 550.00
    assert (s.rent_increase.binding, s.rent_increase.new_rent) == ("ortsübliche Vergleichsmiete", Decimal("550.00"))


def test_the_request_is_its_own_letter_and_says_nothing_the_result_does_not() -> None:
    parsed, s = _settled(_case())
    letters = _drafts(parsed, s)
    rent = letters[-1]
    assert rent.title.startswith("Mieterhöhungsverlangen") and rent.recipient == "Familie Brettschneider"
    assert "bis zum 31.05.2026" in rent.text and "ab dem 01.06.2026" in rent.text
    assert "540,00 € auf 575,00 €" in rent.text and "605,00 €" in rent.text
    result = {k: v for k, v in plain(s).items() if k not in ("questions", "assumptions")}
    for letter in letters:
        assert amounts_not_in(letter.text + "\n" + letter.enclosure, result) == []


def test_without_the_assessed_cap_or_the_waiting_period_there_is_no_result_but_a_question() -> None:
    _, s = _settled(_case(cap=None))
    assert not s.ready and any("Kappungsgrenze" in q for q in s.questions)
    recent = [*RENTS[:1], {"start": "2025-08-01", "rent": "540.00", "surcharge": "0.00", "basis": "558"}]
    _, s = _settled(_case(rents=recent))
    assert not s.ready and any("§ 558 Abs. 1 BGB" in q for q in s.questions)


def test_a_modernisation_step_keeps_the_base_rent_however_its_rent_was_read() -> None:
    # Seen on NK-03 with the real model: a § 559 line read once as the base rent plus surcharge,
    # once as the total. A § 559 increase adds a surcharge and leaves the base rent as it was.
    inclusive = [*RENTS[:2], {"start": "2025-09-01", "rent": "570.00", "surcharge": "30.00", "basis": "559"}]
    for rents in (RENTS, inclusive):
        _, s = _settled(_case(rents=rents))
        assert (s.rent_increase.current_rent, s.rent_increase.surcharge) == (Decimal("540.00"), Decimal("30.00"))
    neither = [*RENTS[:2], {"start": "2025-09-01", "rent": "600.00", "surcharge": "30.00", "basis": "559"}]
    with pytest.raises(InputError, match="01.09.2025"):  # an input to correct, before anything is computed
        _parse(_case(rents=neither), SETTINGS)


def test_the_check_refuses_a_rent_increase_the_facts_do_not_give() -> None:
    parsed, s = _settled(_case())
    raised = dataclasses.replace(s, rent_increase=dataclasses.replace(s.rent_increase, new_rent=Decimal("600.00")))
    problems = verify(parsed.case, raised, CATALOGUE)
    assert any("new_rent" in p for p in problems)


def test_without_the_local_rent_there_is_no_increase_but_a_question_for_its_justification() -> None:
    # Seen on an adversarial variant of NK-03 (the Mietspiegel removed): the reader left the figure
    # empty, and the case failed as "an invalid value" instead of saying what is missing.
    _, s = _settled(_case(local=""))
    assert not s.ready and s.rent_increase is None
    assert any("Vergleichsmiete" in q and "§ 558a" in q for q in s.questions)
