"""The postcondition of a settlement: re-derive its invariants from the `Case` and the catalogue,
independently of how the calculator reached them. An empty list means it holds.

Two layers. The first checks the settlement against itself (lines add up, the Probe). The second
(`_independent`) re-derives from the documents and the catalogue alone what may be charged, each
party's share by time, which periods are vacancy and what each tenant paid, so a calculator bug
cannot pass by agreeing with itself (red flag C7). Heating is split by the meter readings and is
checked for its sum and sign, not re-derived.

This is what makes the settlement safe to hand to a reviewer, and later to a model that writes
the letters: a figure that does not trace back to the documents, a position that went missing, or
shares that do not add up are caught here, whatever produced the settlement.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from betriebskosten.calculate import (
    NOT_PASSED_ON,
    PARTLY_PASSED_ON,
    Catalogue,
)
from betriebskosten.model import VACANCY, Case, Position, Settlement


def verify(case: Case, settlement: Settlement, catalogue: Catalogue) -> list[str]:
    if not settlement.ready:
        return ["the settlement has open questions"]

    problems: list[str] = []
    positions = {p.id: p for p in case.positions}
    problems += _independent(case, settlement, catalogue)
    problems += _rent(case, settlement)
    problems += _late(case, settlement)

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
        if statement.final and statement.balance != statement.total - statement.advances:
            problems.append(f"{statement.party}: balance is not total minus advances")
        if not statement.final and statement.balance is not None:
            problems.append(f"{statement.party}: a balance is stated while a share is open")

    for pid, position in positions.items():
        lines = [line for s in settlement.statements for line in s.lines if line.position_id == pid]
        allocable = {line.allocable for line in lines}
        if len(allocable) != 1:
            problems.append(f"{position.label}: statements disagree on the allocable amount")
            continue
        (amount,) = allocable
        still_open = sum(
            (o.amount for o in settlement.open_items if o.position_id == pid), Decimal("0.00")
        )
        if sum((line.share for line in lines), Decimal("0.00")) + still_open != amount:
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
    totals += sum((o.amount for o in settlement.open_items), Decimal("0.00"))
    if totals != settlement.allocable_total:
        problems.append("Probe: the parties' totals and what is open do not add up to the allocable costs")
    problems += _open_items(case, settlement)

    paid = {t.tenant: t for t in case.tenancies}
    for statement in settlement.statements:
        tenancy = paid.get(statement.party)
        if tenancy is None:
            continue
        if tenancy.advances_paid is not None and statement.advances != tenancy.advances_paid:
            problems.append(f"{statement.party}: advances differ from the documented payments")
    return problems


def _open_items(case: Case, settlement: Settlement) -> list[str]:
    """Open is allowed only as a heating pool the norm splits once one fact is in, and only when
    the item says what is missing, from whom, by when, and by which method (Lessons Learned
    NK 01/02, § 6). Exactly the parties sharing an open pool have no final statement."""
    problems: list[str] = []
    heating_id = case.heating.position_id if case.heating else None
    for o in settlement.open_items:
        if o.position_id != heating_id:
            problems.append(f"{o.position_id}: only the heating split can stay open")
        if not (o.what.strip() and o.addressee.strip() and o.method.strip()):
            problems.append(f"{o.position_id}: an open item names what, from whom and how")
        if o.due > settlement.deadline:
            problems.append(f"{o.position_id}: the open item is due after the § 556 deadline")
        if o.amount <= 0:
            problems.append(f"{o.position_id}: an open item is a positive amount")
    for st in settlement.statements:
        shares_open = any(st.start <= o.end and o.start <= st.end for o in settlement.open_items)
        if st.final == shares_open:
            problems.append(f"{st.party}: final, although it shares an open item, or the reverse")
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

    time = _time(statements)
    period_time = sum(time.values())
    heating_id = case.heating.position_id if case.heating else None
    for p in case.positions:
        expected = _allocable(p, case, catalogue)
        lines = [(st, line) for st in statements for line in st.lines if line.position_id == p.id]
        if any(line.allocable != expected for _, line in lines):
            problems.append(f"{p.label}: allocable amount is not what the catalogue allows ({expected})")
        if any(line.share < 0 for _, line in lines):
            problems.append(f"{p.label}: a party's share is negative")
        if p.id == heating_id or not period_time:
            continue  # heating follows the meter readings; its sum is checked above
        for st, line in lines:
            exact = Fraction(int(expected * 100) * time[id(st)], period_time)
            if abs(Fraction(int(line.share * 100)) - exact) >= 1:
                problems.append(f"{st.party}: {p.label} is not their share by time")

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


def _three_months_on(d: date) -> date:
    month = d.month + 3
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _late(case: Case, settlement: Settlement) -> list[str]:
    """§ 556 Abs. 3 Satz 3 BGB re-derived from the case and its assessment: what stays claimable,
    what is lost, what is paid or refunded."""
    try:
        deadline = case.period_end.replace(year=case.period_end.year + 1)
    except ValueError:
        deadline = case.period_end.replace(year=case.period_end.year + 1, day=28)
    late = case.today is not None and case.today > deadline
    if not late:
        return ["a late settlement is stated for one made in time"] if settlement.late else []
    if settlement.late is None or case.late is None:
        return ["a settlement after its deadline does not say what stays claimable"]
    exempt = {
        p.position_id for p in case.late.positions
        if not p.responsible and p.could_bill_from and p.obstacle != "weg_abrechnung"  # BGH VIII ZR 249/15
        and case.today <= _three_months_on(p.could_bill_from)
    }
    problems = []
    for s in settlement.statements:
        if s.party == VACANCY or s.balance is None:
            continue
        claimable = Decimal("0.00")
        for line in s.lines:
            expected = line.share if line.position_id in exempt and line.allocable > 0 else Decimal("0.00")
            claimable += expected
            if line.claimable != expected:
                problems.append(f"{s.party}: {line.label} claimable {line.claimable}, the assessment gives {expected}")
        rest = s.total - claimable - s.advances
        lost = rest if rest > 0 else Decimal("0.00")
        payable = claimable + (rest if rest < 0 else Decimal("0.00"))
        if (s.claimable, s.lost, s.payable) != (claimable, lost, payable):
            problems.append(f"{s.party}: claimable/lost/payable {s.claimable}/{s.lost}/{s.payable}, the case gives {claimable}/{lost}/{payable}")
    return problems


def _rent(case: Case, settlement: Settlement) -> list[str]:
    """§ 558 BGB, re-derived from the facts and the assessed cap alone, by day counts rather than
    the calculator's month arithmetic: a rent increase that does not follow from them is refused."""
    r, got = case.rent_increase, settlement.rent_increase
    if r is None:
        return ["a rent increase is stated that the case does not ask for"] if got else []
    if got is None:
        return ["the case asks for a rent increase, and none is stated"]
    problems = []
    # The start of the third month after receipt: step over the month ends one by one.
    d = r.delivered
    for _ in range(3):
        d = d.replace(day=1) + timedelta(days=32)
    effective = d.replace(day=1)
    consent_by = effective - timedelta(days=1)
    base_date = date(effective.year - 3, effective.month, 1)
    base = next((s.rent for s in reversed(r.rents) if s.start <= base_date), None)
    now = next((s for s in reversed(r.rents) if s.start <= effective), None)
    if base is None or now is None or r.cap_percent is None:
        return ["the rent increase rests on a rent or a cap the case does not have"]
    cap = (base * (100 + r.cap_percent) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    local = (r.area_sqm * r.local_rent_per_sqm).quantize(Decimal("0.01"), ROUND_HALF_UP)
    allowed = cap if cap < local else local
    expected = {
        "effective": effective, "consent_by": consent_by, "base_rent": base, "cap_limit": cap,
        "local_rent": local, "current_rent": now.rent, "surcharge": now.surcharge, "new_rent": allowed,
        "new_total": allowed + now.surcharge, "increase": allowed - now.rent,
        "current_total": now.rent + now.surcharge,
    }
    for name, value in expected.items():
        if getattr(got, name) != value:
            problems.append(f"rent increase: {name} is {getattr(got, name)}, the facts give {value}")
    if got.new_rent > local or got.new_rent > cap:
        problems.append("rent increase: above the local rent or the cap")
    return problems


def _allocable(p: Position, case: Case, catalogue: Catalogue) -> Decimal:
    """What tenants may be charged of `p`, from the catalogue and the lease alone."""
    category = catalogue.categories[p.category or ""]
    allocable = category.allocable
    if case.lease.assessed is not None and allocable and (case.lease.kind == "closed_list" or allocable == "agreement"):
        # The lease as the assessment read it: a cost type it does not charge is never passed on.
        allocable = category.key in case.lease.covered
    if allocable == "agreement":
        allocable = category.key in case.lease.named_other_costs
    if allocable and category.allocable_until is not None:
        allocable = case.period_start <= category.allocable_until
    if not allocable:
        return Decimal("0.00")
    return p.amount - sum((e.amount for e in p.excluded), Decimal("0.00"))


def _time(statements) -> dict[int, int]:
    """Each statement's weight by time: whole months when every statement is whole months,
    otherwise days."""
    months = {id(st): _months(st.start, st.end) for st in statements}
    if all(m is not None for m in months.values()):
        return months
    return {id(st): (st.end - st.start).days + 1 for st in statements}


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
