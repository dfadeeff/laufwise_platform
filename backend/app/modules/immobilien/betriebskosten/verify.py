"""The postcondition of a settlement: re-derive its invariants from the `Case` and the catalogue,
independently of how the calculator reached them. An empty list means it holds.

Two layers. The first checks the settlement against itself (lines add up, the Probe). The second
(`_independent`) re-derives from the documents and the catalogue alone what may be charged, each
party's share by days, which periods are vacancy and what each tenant paid, so a calculator bug
cannot pass by agreeing with itself (red flag C7). Heating is split by the meter readings and is
checked for its sum and sign, not re-derived.

This is what makes the settlement safe to hand to a reviewer, and later to a model that writes
the letters: a figure that does not trace back to the documents, a position that went missing, or
shares that do not add up are caught here, whatever produced the settlement.
"""

from __future__ import annotations

import calendar
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

from app.modules.immobilien.betriebskosten.calculate import (
    NOT_PASSED_ON,
    PARTLY_PASSED_ON,
    Catalogue,
)
from app.modules.immobilien.betriebskosten.model import VACANCY, Case, Position, Settlement


def verify(case: Case, settlement: Settlement, catalogue: Catalogue) -> list[str]:
    if not settlement.ready:
        return ["the settlement has open questions"]

    problems: list[str] = []
    positions = {p.id: p for p in case.positions}
    problems += _independent(case, settlement, catalogue)

    for statement in settlement.statements:
        ids = [line.position_id for line in statement.lines]
        if sorted(ids) != sorted(positions):
            problems.append(f"{statement.party}: positions differ from the documents")
        for line in statement.lines:
            source = positions.get(line.position_id)
            if source is None or line.amount != source.amount:
                problems.append(f"{statement.party}: {line.label} is not the documented amount")
        total = sum((line.share for line in statement.lines), Decimal("0.00"))
        if statement.total != total:
            problems.append(f"{statement.party}: total is not the sum of its lines")
        if statement.balance != statement.total - statement.advances:
            problems.append(f"{statement.party}: balance is not total minus advances")

    for pid, position in positions.items():
        lines = [line for s in settlement.statements for line in s.lines if line.position_id == pid]
        allocable = {line.allocable for line in lines}
        if len(allocable) != 1:
            problems.append(f"{position.label}: statements disagree on the allocable amount")
            continue
        (amount,) = allocable
        if sum((line.share for line in lines), Decimal("0.00")) != amount:
            problems.append(f"{position.label}: shares do not add up to the allocable amount")
        held_back = sum(
            (
                f.amount
                for f in settlement.protocol
                if f.position_id == pid and f.treatment in (NOT_PASSED_ON, PARTLY_PASSED_ON)
            ),
            Decimal("0.00"),
        )
        if amount + held_back != position.amount:
            problems.append(f"{position.label}: allocable plus held back is not the amount")
        if not Decimal("0.00") <= amount <= position.amount:
            problems.append(f"{position.label}: allocable amount outside 0 and the amount")
        if any(e.amount <= 0 for e in position.excluded):
            problems.append(f"{position.label}: a deduction is not a positive amount")

    totals = sum((s.total for s in settlement.statements), Decimal("0.00"))
    if totals != settlement.allocable_total:
        problems.append("Probe: the parties' totals do not add up to the allocable costs")

    paid = {t.tenant: t for t in case.tenancies}
    for statement in settlement.statements:
        tenancy = paid.get(statement.party)
        if tenancy is None:
            continue
        if tenancy.advances_paid is not None and statement.advances != tenancy.advances_paid:
            problems.append(f"{statement.party}: advances differ from the documented payments")
    return problems


# --- re-derived from the case and the catalogue, never from the calculator (red flag C7) ---


def _independent(case: Case, settlement: Settlement, catalogue: Catalogue) -> list[str]:
    problems: list[str] = []
    statements = sorted(settlement.statements, key=lambda s: s.start)

    # The parties cover the period exactly: no gap, no overlap, tenancies as documented, and
    # everything else is vacancy.
    cursor = case.period_start
    for st in statements:
        if st.start != cursor:
            problems.append(f"{st.party}: the statements do not cover the period without a gap")
        cursor = st.end + timedelta(days=1)
    if cursor != case.period_end + timedelta(days=1):
        problems.append("the statements do not reach the end of the period")
    # A tenancy is held as the documents state it (an earlier start, no end); the part inside
    # the period is derived here again rather than read from the calculator.
    tenancies = {(t.tenant, *_in_period(case, t)) for t in case.tenancies}
    for st in statements:
        if st.party != VACANCY and (st.party, st.start, st.end) not in tenancies:
            problems.append(f"{st.party}: not a documented tenancy")
    if len([st for st in statements if st.party != VACANCY]) != len(tenancies):
        problems.append("a documented tenancy has no statement")

    days = {id(st): (st.end - st.start).days + 1 for st in statements}
    period_days = sum(days.values())
    heating_id = case.heating.position_id if case.heating else None
    for p in case.positions:
        expected = _allocable(p, case, catalogue)
        lines = [(st, line) for st in statements for line in st.lines if line.position_id == p.id]
        if any(line.allocable != expected for _, line in lines):
            problems.append(f"{p.label}: allocable amount is not what the catalogue allows ({expected})")
        if any(line.share < 0 for _, line in lines):
            problems.append(f"{p.label}: a party's share is negative")
        if p.id == heating_id or not period_days:
            continue  # heating follows the meter readings; its sum is checked above
        for st, line in lines:
            exact = Fraction(int(expected * 100) * days[id(st)], period_days)
            if abs(Fraction(int(line.share * 100)) - exact) >= 1:
                problems.append(f"{st.party}: {p.label} is not their share by days")

    for st in statements:
        if st.party == VACANCY:
            if st.advances != 0:
                problems.append("the vacancy is credited advances")
            continue
        tenancy = next((t for t in case.tenancies if t.tenant == st.party), None)
        if tenancy is None:
            continue
        if tenancy.advances_paid is not None:
            expected_paid = tenancy.advances_paid
        else:
            months = _months(*_in_period(case, tenancy))
            expected_paid = tenancy.monthly_advance * months if months else None
        if expected_paid is not None and st.advances != expected_paid:
            problems.append(f"{st.party}: advances are not what the tenancy documents")
    return problems


def _allocable(p: Position, case: Case, catalogue: Catalogue) -> Decimal:
    """What tenants may be charged of `p`, from the catalogue and the lease alone."""
    category = catalogue.categories[p.category or ""]
    allocable = category.allocable
    if allocable == "agreement":
        allocable = category.key in case.lease.named_other_costs
    if allocable and category.allocable_until is not None:
        allocable = case.period_start <= category.allocable_until
    if not allocable:
        return Decimal("0.00")
    return p.amount - sum((e.amount for e in p.excluded), Decimal("0.00"))


def _in_period(case: Case, t) -> tuple:
    """The part of a tenancy inside the period. A tenancy is held as the documents state it (an
    earlier start, no end); this is derived here again, not read from the calculator."""
    return (
        max(t.start or case.period_start, case.period_start),
        min(t.end or case.period_end, case.period_end),
    )


def _months(start, end) -> int | None:
    if start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1
