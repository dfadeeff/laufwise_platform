"""Compute a Kautionsabrechnung from a `Deposit`. Deterministic: no model, no I/O (ADR-0036).

What may be charged comes from the catalogue; this file applies it. Anything the documents leave
open, or that needs a person's legal judgment, becomes a question, and a settlement with
questions carries no figures.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from app.modules.immobilien.kaution.model import (
    NOT_CHARGED,
    OFFSET,
    RETAINED,
    Deposit,
    DepositSettlement,
    Item,
)
from app.modules.immobilien.letters import euro

CATALOGUE_PATH = Path(__file__).with_name("catalogue.yaml")
ZERO = Decimal("0.00")
# Claims for changes to the flat itself, which § 548 Abs. 1 BGB lets expire six months after return.
_CHANGES_TO_THE_FLAT = {"schadensersatz", "schoenheitsreparaturen"}


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    treatment: str  # offset, retain, never
    citation: str
    requires_evidence: bool = False
    requires_clause: bool = False


@dataclass(frozen=True)
class Catalogue:
    version: int
    categories: dict[str, Category]


def load_catalogue(path: Path = CATALOGUE_PATH) -> Catalogue:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Catalogue(
        version=raw["version"],
        categories={
            key: Category(
                key=key,
                label=c["label"],
                treatment=c["treatment"],
                citation=c["citation"],
                requires_evidence=c.get("requires_evidence", False),
                requires_clause=c.get("requires_clause", False),
            )
            for key, c in raw["categories"].items()
        },
    )


def six_months_after(day: date) -> date:
    month = day.month + 6
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def settle(deposit: Deposit, catalogue: Catalogue) -> DepositSettlement:
    questions: list[str] = []
    assumptions: list[str] = []
    items: list[Item] = []

    paid = sum((p.amount for p in deposit.payments), ZERO)
    if not deposit.payments:
        questions.append("Es ist keine Kautionszahlung angegeben.")
    elif paid > deposit.agreed:
        questions.append(
            f"Gezahlt wurden {euro(paid)}, mehr als die vereinbarte Kaution von "
            f"{euro(deposit.agreed)}. Bitte die Zahlungen prüfen."
        )
    elif paid < deposit.agreed:
        assumptions.append(
            f"Gezahlt wurden {euro(paid)} von vereinbarten {euro(deposit.agreed)}; abgerechnet "
            f"wird über das tatsächlich Gezahlte."
        )
    if deposit.monthly_rent_net and deposit.agreed > 3 * deposit.monthly_rent_net:
        assumptions.append(
            f"Die vereinbarte Kaution von {euro(deposit.agreed)} übersteigt drei Nettokaltmieten "
            f"({euro(3 * deposit.monthly_rent_net)}, § 551 Abs. 1 BGB); den übersteigenden Teil "
            f"kann der Mieter ohnehin zurückverlangen."
        )
    if deposit.returned_on is None:
        questions.append(
            "Das Datum der Rückgabe der Wohnung fehlt; nach ihm richten sich die Verjährung "
            "(§ 548 BGB) und die Frist für diese Abrechnung."
        )

    for claim in deposit.claims:
        category = catalogue.categories.get(claim.category or "")
        if category is None:
            questions.append(
                f"„{claim.label}“ ({euro(claim.amount)}, {claim.source}) ist keiner bekannten "
                f"Art von Forderung zugeordnet."
            )
            continue
        if category.treatment == "never":
            items.append(
                Item(claim.id, claim.label, claim.amount, NOT_CHARGED,
                     f"{category.label}: wird nicht mit der Kaution verrechnet.", category.citation)
            )
            continue
        if category.requires_clause:
            if deposit.renovation_clause is None:
                questions.append(
                    f"„{claim.label}“: ob die Schönheitsreparatur-Klausel des Mietvertrags wirksam "
                    f"ist, muss eine Person beurteilen."
                )
                continue
            if deposit.renovation_clause == "unwirksam":
                items.append(
                    Item(claim.id, claim.label, claim.amount, NOT_CHARGED,
                         "Die Schönheitsreparatur-Klausel des Mietvertrags ist unwirksam.",
                         category.citation)
                )
                continue
        if category.requires_evidence and not claim.evidence:
            questions.append(
                f"„{claim.label}“ ({euro(claim.amount)}): es fehlt der Nachweis (Rechnung, "
                f"Angebot oder Eintrag im Übergabeprotokoll)."
            )
            continue
        items.append(
            Item(
                claim.id,
                claim.label,
                claim.amount,
                RETAINED if category.treatment == "retain" else OFFSET,
                f"{category.label}; Nachweis: {claim.evidence}" if claim.evidence else category.label,
                category.citation,
            )
        )

    deadline = six_months_after(deposit.returned_on) if deposit.returned_on else None
    changes = {c.id for c in deposit.claims if c.category in _CHANGES_TO_THE_FLAT}
    if deadline and any(i.treatment == OFFSET and i.claim_id in changes for i in items):
        assumptions.append(
            f"Ansprüche wegen Veränderungen oder Verschlechterungen der Wohnung verjähren sechs "
            f"Monate nach der Rückgabe, hier am {deadline:%d.%m.%Y} (§ 548 Abs. 1 BGB)."
        )

    if questions:
        return DepositSettlement(
            deposit.tenant, deposit.unit, paid, deposit.interest, deposit.tax_withheld, ZERO,
            (), ZERO, ZERO, ZERO, ZERO, deadline, tuple(assumptions), tuple(questions),
        )

    credit = paid + deposit.interest - deposit.tax_withheld
    offset = sum((i.amount for i in items if i.treatment == OFFSET), ZERO)
    retained = sum((i.amount for i in items if i.treatment == RETAINED), ZERO)
    balance = credit - offset - retained
    if retained:
        assumptions.append(
            f"{euro(retained)} werden bis zur ausstehenden Betriebskostenabrechnung einbehalten "
            f"und danach abgerechnet."
        )
    return DepositSettlement(
        tenant=deposit.tenant,
        unit=deposit.unit,
        paid=paid,
        interest=deposit.interest,
        tax_withheld=deposit.tax_withheld,
        credit=credit,
        items=tuple(items),
        offset=offset,
        retained=retained,
        payout=max(balance, ZERO),
        remaining_claim=max(-balance, ZERO),
        deadline=deadline,
        assumptions=tuple(assumptions),
    )
