"""What the lease charges, as the case's assessment reads it (ADR-0042, version 6).

The model reads the lease and says, per cost type, whether it is charged to the tenant, its clause
quoted; the code applies that. A cost the law allows but the lease does not name is not passed on,
and the independent check holds the settlement to the same reading. A cost type the assessment did
not answer is a question, not a guess. Made-up variations of NK-01, not an expert's case.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from betriebskosten.calculate import settle
from betriebskosten.verify import verify
from betriebskosten.workflow import _parse
from test_betriebskosten_gold import CATALOGUE, SETTINGS, _raw

CLAUSE = "Neben der Grundmiete trägt der Mieter die folgenden Betriebskosten: die Grundsteuer, die Kosten der Wasserversorgung."


def _assessed(raw: dict, covered: set[str], kind: str = "closed_list", leave_out: set[str] = frozenset()) -> dict:
    basis = {"source": "Mietvertrag", "quote": CLAUSE}
    cats = sorted({p["category"] for p in raw["positions"] if p.get("category")} - leave_out)
    return {**raw, "assessment": {"lease": {"kind": kind, "basis": basis, "categories": [
        {"category": c, "covered": c in covered, "basis": basis} for c in cats]}}}


def _settle(raw: dict):
    parsed = _parse(raw, SETTINGS)
    return parsed, settle(parsed.case, CATALOGUE, parsed.policy)


def _line(settlement, category_label: str):
    (statement,) = settlement.statements
    return next(line for line in statement.lines if line.label.startswith(category_label))


def test_a_cost_the_lease_does_not_name_is_not_passed_on_with_its_clause_in_the_protocol() -> None:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    parsed, settlement = _settle(_assessed(raw, every - {"gartenpflege"}))

    assert _line(settlement, "Gartenpflege").allocable == Decimal("0.00")
    finding = next(f for f in settlement.protocol if f.label.startswith("Gartenpflege"))
    assert "im Mietvertrag nicht vereinbart" in finding.reason and CLAUSE[:40] in finding.citation
    assert verify(parsed.case, settlement, CATALOGUE) == []  # the independent check reads the lease the same way

    _, every_named = _settle(_assessed(raw, every))
    garden = _line(every_named, "Gartenpflege").allocable
    assert garden > 0 and every_named.allocable_total - settlement.allocable_total == garden


def test_a_reading_that_covers_everything_settles_as_before() -> None:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    _, before = _settle(raw)
    _, assessed = _settle(_assessed(raw, every, kind="reference_to_betrkv"))
    assert assessed.allocable_total == before.allocable_total == Decimal("2331.40")


def test_a_cost_type_the_assessment_did_not_answer_is_a_question() -> None:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    _, settlement = _settle(_assessed(raw, every, leave_out={"gartenpflege"}))
    assert not settlement.ready
    assert any("ob der Mietvertrag" in q and "Gartenpflege" in q for q in settlement.questions)


def test_the_check_refuses_a_settlement_that_charges_what_the_lease_does_not() -> None:
    raw = _raw("nk01")
    every = {p["category"] for p in raw["positions"] if p.get("category")}
    _, charged = _settle(_assessed(raw, every))  # computed as if the lease named the garden
    narrow, _ = _settle(_assessed(copy.deepcopy(raw), every - {"gartenpflege"}))
    assert verify(narrow.case, charged, CATALOGUE)  # the check reads the lease as assessed: refused
