"""The postcondition of a settlement: re-derive its invariants from the `Case`, independently of
how the calculator reached them. An empty list means it holds.

This is what makes the settlement safe to hand to a reviewer, and later to a model that writes
the letters: a figure that does not trace back to the documents, a position that went missing, or
shares that do not add up are caught here, whatever produced the settlement.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.immobilien.betriebskosten.calculate import NOT_PASSED_ON, PARTLY_PASSED_ON
from app.modules.immobilien.betriebskosten.model import Case, Settlement


def verify(case: Case, settlement: Settlement) -> list[str]:
    if not settlement.ready:
        return ["the settlement has open questions"]

    problems: list[str] = []
    positions = {p.id: p for p in case.positions}

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
