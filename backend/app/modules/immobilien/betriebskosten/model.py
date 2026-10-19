"""The data a Betriebskostenabrechnung is computed from, and the settlement it produces.

`Case` is the target of extraction: whatever reads the documents (a person today, a model call
later) fills exactly these fields, and every amount carries the place it was read from. Nothing
downstream reads a document again, so a figure that is not in a `Case` cannot reach a tenant.

Money is held as `Decimal` with two places; the calculator works in whole cents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

VACANCY = "Vermieter (Leerstand)"


@dataclass(frozen=True)
class Exclusion:
    """A part of a position's amount that belongs to another category, e.g. small repairs inside
    the caretaker's bill."""

    amount: Decimal
    category: str
    source: str


@dataclass(frozen=True)
class Position:
    id: str
    label: str  # as written in the source document
    amount: Decimal
    category: str | None  # catalogue key; None means nobody has classified it yet
    source: str
    # How the owners' association split this cost between units: "mea", "flaeche", "verbrauch"
    # or "direkt" (billed to this unit alone). None when the document does not say.
    weg_key: str | None = None
    excluded: tuple[Exclusion, ...] = ()


@dataclass(frozen=True)
class Tenancy:
    tenant: str
    start: date
    end: date  # inclusive
    monthly_advance: Decimal
    advances_paid: Decimal | None = None  # None: not confirmed, the schedule is assumed paid


@dataclass(frozen=True)
class Reading:
    """The share of the year's heat consumption metered up to and including `end`, counted from
    the day after the previous reading."""

    end: date
    share: Decimal


@dataclass(frozen=True)
class Heating:
    position_id: str
    base_share: Decimal  # Grundkosten, e.g. 0.40; the rest is split by consumption
    readings: tuple[Reading, ...] = ()
    hot_water_separate: bool = False


@dataclass(frozen=True)
class Lease:
    key: str  # how the lease says non-heating costs are split, e.g. "flaeche"
    named_other_costs: tuple[str, ...] = ()  # § 2 Nr. 17 BetrKV items the lease names explicitly


@dataclass(frozen=True)
class Case:
    unit: str
    period_start: date
    period_end: date
    positions: tuple[Position, ...]
    tenancies: tuple[Tenancy, ...]
    lease: Lease
    heating: Heating | None = None


@dataclass(frozen=True)
class Policy:
    """Decisions a person takes once per workspace, not per settlement.

    `basis`: what to do when the association split a cost by a different key than the lease
    names. None asks every time; "unit_share" takes the association's figure for the unit as the
    unit's cost and says so in the settlement.
    """

    basis: str | None = None


@dataclass(frozen=True)
class Line:
    position_id: str
    label: str
    amount: Decimal  # the position's amount as billed to the unit
    allocable: Decimal  # the part of it tenants can be charged for, whole year
    share: Decimal  # this party's part of `allocable`
    key: str  # how `share` was reached, in words


@dataclass(frozen=True)
class Statement:
    party: str
    start: date
    end: date
    lines: tuple[Line, ...]
    total: Decimal
    advances: Decimal
    balance: Decimal  # positive: the tenant pays; negative: the tenant is refunded


@dataclass(frozen=True)
class Finding:
    """One entry of the Prüfprotokoll: a position not, or only partly, passed on, or passed on by
    a special key."""

    position_id: str
    label: str
    treatment: str
    amount: Decimal  # the amount this finding is about
    reason: str
    citation: str


@dataclass(frozen=True)
class Proposal:
    tenant: str
    current: Decimal
    basis_total: Decimal
    proposed: Decimal
    reasoning: str


@dataclass(frozen=True)
class Settlement:
    statements: tuple[Statement, ...]  # one per tenant, then the vacancy share if there is one
    protocol: tuple[Finding, ...]
    allocable_total: Decimal
    proposal: Proposal | None
    deadline: date  # § 556 Abs. 3 BGB: the tenants must have it by then
    assumptions: tuple[str, ...]
    questions: tuple[str, ...] = field(default=())  # anything here blocks the settlement

    @property
    def ready(self) -> bool:
        return not self.questions


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def parse_case(raw: dict[str, Any]) -> Case:
    """Build a `Case` from its plain-data form (YAML or JSON, as extraction will emit it)."""
    heating = raw.get("heating")
    return Case(
        unit=raw["unit"],
        period_start=_day(raw["period_start"]),
        period_end=_day(raw["period_end"]),
        positions=tuple(
            Position(
                id=p["id"],
                label=p["label"],
                amount=_money(p["amount"]),
                category=p.get("category"),
                source=p["source"],
                weg_key=p.get("weg_key"),
                excluded=tuple(
                    Exclusion(_money(e["amount"]), e["category"], e["source"])
                    for e in p.get("excluded", ())
                ),
            )
            for p in raw["positions"]
        ),
        tenancies=tuple(
            Tenancy(
                tenant=t["tenant"],
                start=_day(t["start"]),
                end=_day(t["end"]),
                monthly_advance=_money(t["monthly_advance"]),
                advances_paid=(
                    _money(t["advances_paid"]) if t.get("advances_paid") is not None else None
                ),
            )
            for t in raw["tenancies"]
        ),
        lease=Lease(
            key=raw["lease"]["key"],
            named_other_costs=tuple(raw["lease"].get("named_other_costs", ())),
        ),
        heating=(
            Heating(
                position_id=heating["position_id"],
                base_share=Decimal(str(heating["base_share"])),
                readings=tuple(
                    Reading(_day(r["end"]), Decimal(str(r["share"])))
                    for r in heating.get("readings", ())
                ),
                hot_water_separate=heating.get("hot_water_separate", False),
            )
            if heating
            else None
        ),
    )
