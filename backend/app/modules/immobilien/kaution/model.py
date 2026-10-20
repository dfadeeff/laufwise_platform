"""What a Kautionsabrechnung is computed from, and what it produces (ADR-0036).

`Deposit` holds what the documents state: the lease, the deposit payments, the deposit account's
interest and tax, the landlord's claims with their evidence. Whether a claim may be charged is
not stated here; the catalogue decides it in `calculate`. Money is `Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

OFFSET = "verrechnet"
RETAINED = "einbehalten"
NOT_CHARGED = "nicht verrechnet"


@dataclass(frozen=True)
class Payment:
    date: date | None
    amount: Decimal
    source: str


@dataclass(frozen=True)
class Claim:
    """A claim the landlord wants to settle against the deposit, as the documents name it."""

    id: str
    label: str
    amount: Decimal
    category: str | None  # catalogue key; None: nobody has classified it
    source: str
    evidence: str = ""  # what proves it: an invoice, a quote, an entry in the handover record


@dataclass(frozen=True)
class Deposit:
    tenant: str
    unit: str
    lease_end: date
    returned_on: date | None  # the flat handed back (§ 548 BGB counts from here)
    monthly_rent_net: Decimal | None  # Nettokaltmiete, for the § 551 cap
    agreed: Decimal
    payments: tuple[Payment, ...]
    interest: Decimal  # as the deposit account states it
    tax_withheld: Decimal  # Kapitalertragsteuer and Soli the bank withheld, as stated
    # Whether the lease's cosmetic-repair clause is valid: a legal judgment a person makes,
    # never read from the documents. "wirksam", "unwirksam", or None (not assessed).
    renovation_clause: str | None
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Policy:
    sender: str = ""
    payment_days: int = 30


@dataclass(frozen=True)
class Item:
    claim_id: str
    label: str
    amount: Decimal
    treatment: str  # verrechnet, einbehalten, nicht verrechnet
    reason: str
    citation: str


@dataclass(frozen=True)
class DepositSettlement:
    tenant: str
    unit: str
    paid: Decimal
    interest: Decimal
    tax_withheld: Decimal
    credit: Decimal  # paid + interest − tax withheld
    items: tuple[Item, ...]
    offset: Decimal
    retained: Decimal
    payout: Decimal  # what the tenant receives now
    remaining_claim: Decimal  # what the tenant still owes, when the claims exceed the credit
    deadline: date | None  # orientation: six months after the return
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.questions


class DepositError(ValueError):
    """The input describes something impossible; the messages say what to correct."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


def _money(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise DepositError([f"„{value}“ ist kein Betrag."]) from exc
    if not number.is_finite() or number != number.quantize(Decimal("0.01")):
        raise DepositError([f"„{value}“ ist kein Betrag mit höchstens zwei Nachkommastellen."])
    return number.quantize(Decimal("0.01"))


def _day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def parse_deposit(raw: dict[str, Any]) -> Deposit:
    rent = raw.get("monthly_rent_net")
    lease_end = _day(raw.get("lease_end"))
    if lease_end is None:
        raise DepositError(["Das Ende des Mietverhältnisses fehlt."])
    deposit = Deposit(
        tenant=(raw.get("tenant") or "").strip(),
        unit=(raw.get("unit") or "").strip(),
        lease_end=lease_end,
        returned_on=_day(raw.get("returned_on")),
        monthly_rent_net=_money(rent) if rent not in (None, "") else None,
        agreed=_money(raw["agreed"]),
        payments=tuple(
            Payment(_day(p.get("date")), _money(p["amount"]), p.get("source", ""))
            for p in raw.get("payments", ())
        ),
        interest=_money(raw.get("interest") or "0"),
        tax_withheld=_money(raw.get("tax_withheld") or "0"),
        renovation_clause=raw.get("renovation_clause") or None,
        claims=tuple(
            Claim(
                id=c["id"],
                label=c["label"],
                amount=_money(c["amount"]),
                category=c.get("category") or None,
                source=c.get("source", ""),
                evidence=(c.get("evidence") or "").strip(),
            )
            for c in raw.get("claims", ())
        ),
    )
    problems = []
    if not deposit.tenant:
        problems.append("Der Mieter fehlt.")
    if deposit.agreed <= 0:
        problems.append("Die vereinbarte Kaution muss ein positiver Betrag sein.")
    if any(p.amount <= 0 for p in deposit.payments):
        problems.append("Eine Kautionszahlung muss ein positiver Betrag sein.")
    if deposit.interest < 0 or deposit.tax_withheld < 0 or deposit.tax_withheld > deposit.interest:
        problems.append("Zinsen und einbehaltene Steuer dürfen nicht negativ sein; die Steuer nicht höher als die Zinsen.")
    if any(c.amount <= 0 for c in deposit.claims):
        problems.append("Eine Forderung muss ein positiver Betrag sein.")
    ids = [c.id for c in deposit.claims]
    if len(set(ids)) != len(ids):
        problems.append("Jede Forderung braucht eine eigene Nummer.")
    if deposit.renovation_clause not in (None, "wirksam", "unwirksam"):
        problems.append("Die Schönheitsreparaturklausel ist „wirksam“, „unwirksam“ oder nicht beurteilt.")
    if problems:
        raise DepositError(problems)
    return deposit
