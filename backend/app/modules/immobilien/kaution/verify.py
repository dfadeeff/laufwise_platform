"""The postcondition of a Kautionsabrechnung, re-derived from the deposit and the catalogue,
never from the calculator's own figures (ADR-0036; the rule of red flag C7)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.immobilien.kaution.calculate import Catalogue
from app.modules.immobilien.kaution.model import (
    NOT_CHARGED,
    OFFSET,
    RETAINED,
    Deposit,
    DepositSettlement,
)

ZERO = Decimal("0.00")


def _allowed(category, clause: str | None) -> str:
    if category.treatment == "never" or (category.requires_clause and clause == "unwirksam"):
        return NOT_CHARGED
    return RETAINED if category.treatment == "retain" else OFFSET


def verify(deposit: Deposit, s: DepositSettlement, catalogue: Catalogue) -> list[str]:
    if not s.ready:
        return ["the settlement has open questions"]
    problems: list[str] = []

    paid = sum((p.amount for p in deposit.payments), ZERO)
    if s.paid != paid:
        problems.append("paid is not the sum of the documented payments")
    if (s.interest, s.tax_withheld) != (deposit.interest, deposit.tax_withheld):
        problems.append("interest or tax differ from the deposit account")
    if s.credit != paid + deposit.interest - deposit.tax_withheld:
        problems.append("credit is not paid + interest − tax")

    claims = {c.id: c for c in deposit.claims}
    if sorted(i.claim_id for i in s.items) != sorted(claims):
        problems.append("the items are not exactly the documented claims")
    for item in s.items:
        claim = claims.get(item.claim_id)
        if claim is None:
            continue
        if item.amount != claim.amount:
            problems.append(f"{item.claim_id}: not the documented amount")
        category = catalogue.categories[claim.category or ""]
        expected = _allowed(category, deposit.renovation_clause)
        if item.treatment != expected:
            problems.append(
                f"{item.claim_id} ({category.label}): {item.treatment}, the catalogue allows {expected}"
            )

    if s.offset != sum((i.amount for i in s.items if i.treatment == OFFSET), ZERO):
        problems.append("offset is not the sum of the charged claims")
    if s.retained != sum((i.amount for i in s.items if i.treatment == RETAINED), ZERO):
        problems.append("retained is not the sum of the retained claims")
    if s.payout < 0 or s.remaining_claim < 0 or (s.payout > 0 and s.remaining_claim > 0):
        problems.append("a settlement pays out or leaves a claim, never both, never negative")
    if s.payout - s.remaining_claim != s.credit - s.offset - s.retained:
        problems.append("the payout does not follow from credit, offset and retention")
    return problems
