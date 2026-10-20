"""Compute a Betriebskostenabrechnung from a `Case`. Deterministic: no model, no I/O.

What is allocable comes from the catalogue; this file only applies it. Every split is done in
whole cents with the largest-remainder method, so the parties' shares of a position always add up
to the position exactly and the Probe holds by construction rather than within a tolerance.

Anything the documents do not settle becomes a question, and a settlement with questions carries
no figures: an amount the calculator had to guess at never reaches a tenant.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import yaml

from app.modules.immobilien.betriebskosten.model import (
    VACANCY,
    Case,
    Finding,
    Line,
    Policy,
    Position,
    Proposal,
    Settlement,
    Statement,
    Tenancy,
)

CATALOGUE_PATH = Path(__file__).with_name("catalogue.yaml")

NOT_PASSED_ON = "nicht umgelegt"
PARTLY_PASSED_ON = "teilweise umgelegt"
SPECIAL_KEY = "besonderer Maßstab"

_VACANCY_CITATION = "BGH, Urt. v. 31.05.2006 – VIII ZR 159/05"
_HEATING_CHANGE_CITATION = "§ 9b HeizkostenV"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    allocable: bool | str  # True, False or "agreement"
    citation: str
    split: str | None = None  # "heizkostenv" or None (by time)
    allocable_until: date | None = None


@dataclass(frozen=True)
class Catalogue:
    version: int
    categories: dict[str, Category]
    degree_days: dict[int, Fraction]  # month -> per mille of a heating year


def load_catalogue(path: Path = CATALOGUE_PATH) -> Catalogue:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Catalogue(
        version=raw["version"],
        categories={
            key: Category(
                key=key,
                label=c["label"],
                allocable=c["allocable"],
                citation=c["citation"],
                split=c.get("key"),
                allocable_until=c.get("allocable_until"),
            )
            for key, c in raw["categories"].items()
        },
        degree_days={int(m): Fraction(str(v)) for m, v in raw["degree_days"].items()},
    )


@dataclass(frozen=True)
class _Party:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _de(amount: Decimal) -> str:
    """An amount as a German text prints it, 1.234,56 €: every text a tenant may read uses it."""
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def _cents(amount: Decimal) -> int:
    return int(amount * 100)


def _euros(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def _split(cents: int, weights: list[Fraction]) -> list[int]:
    """Split `cents` in proportion to `weights`, in whole cents that add up to `cents`."""
    total = sum(weights, Fraction(0))
    exact = [cents * w / total for w in weights]
    parts = [math.floor(e) for e in exact]
    by_remainder = sorted(range(len(weights)), key=lambda i: exact[i] - parts[i], reverse=True)
    for i in by_remainder[: cents - sum(parts)]:
        parts[i] += 1
    return parts


def _overlap(party: _Party, start: date, end: date) -> tuple[date, date] | None:
    lo, hi = max(party.start, start), min(party.end, end)
    return (lo, hi) if lo <= hi else None


def _in_period(case: Case, t: Tenancy) -> tuple[date, date] | None:
    """The part of a tenancy inside the billing period; None if it has none."""
    start = max(t.start or case.period_start, case.period_start)
    end = min(t.end or case.period_end, case.period_end)
    return (start, end) if start <= end else None


def _stated(t: Tenancy) -> tuple[str, str]:
    return f"{t.start:%d.%m.%Y}" if t.start else "?", f"{t.end:%d.%m.%Y}" if t.end else "?"


def _parties(case: Case, questions: list[str], assumptions: list[str]) -> list[_Party]:
    """The tenancies inside the period in date order, with every gap between them as vacancy."""
    parties: list[_Party] = []
    cursor = case.period_start
    spans = []
    for t in case.tenancies:
        span = _in_period(case, t)
        if span is None:
            start, end = _stated(t)
            questions.append(
                f"Mietzeit von {t.tenant} ({start}–{end}) liegt nicht innerhalb des "
                f"Abrechnungszeitraums."
            )
            continue
        if t.start is None:
            assumptions.append(
                f"Der Beginn des Mietverhältnisses von {t.tenant} ist nicht angegeben; es ist ab "
                f"Beginn des Abrechnungszeitraums ({case.period_start:%d.%m.%Y}) angesetzt."
            )
        spans.append((span, t))
    for (start, end), t in sorted(spans, key=lambda s: s[0][0]):
        if start < cursor:
            questions.append(f"Die Mietzeit von {t.tenant} überschneidet sich mit einer anderen.")
            continue
        if start > cursor:
            parties.append(_Party(VACANCY, cursor, start - timedelta(days=1)))
        parties.append(_Party(t.tenant, start, end))
        cursor = end + timedelta(days=1)
    if cursor <= case.period_end:
        parties.append(_Party(VACANCY, cursor, case.period_end))
    return parties


def _whole_months(start: date, end: date) -> int | None:
    if start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _allocable(
    p: Position, case: Case, catalogue: Catalogue, protocol: list[Finding], questions: list[str]
) -> Decimal | None:
    """The part of `p` tenants can be charged for, recording in `protocol` what is held back."""
    category = catalogue.categories.get(p.category or "")
    if category is None:
        questions.append(
            f"„{p.label}“ ({_de(p.amount)}, {p.source}) ist keiner bekannten Kostenart zugeordnet."
        )
        return None

    allocable = category.allocable
    reason = f"keine umlagefähige Betriebskostenart ({category.label})"
    if allocable == "agreement":
        allocable = category.key in case.lease.named_other_costs
        reason = f"{category.label}: sonstige Betriebskosten, im Mietvertrag nicht vereinbart"
    if allocable and category.allocable_until is not None:
        if case.period_start > category.allocable_until:
            allocable = False
            reason = f"{category.label}: Umlage nur bis {category.allocable_until:%d.%m.%Y}"
        elif case.period_end > category.allocable_until:
            questions.append(
                f"„{p.label}“ ist nur bis {category.allocable_until:%d.%m.%Y} umlagefähig; der "
                f"Abrechnungszeitraum reicht darüber hinaus."
            )
            return None

    if not allocable:
        protocol.append(
            Finding(p.id, p.label, NOT_PASSED_ON, p.amount, reason, category.citation)
        )
        return Decimal("0.00")

    held_back = Decimal("0.00")
    for part in p.excluded:
        part_category = catalogue.categories.get(part.category)
        if part_category is None or part_category.allocable is not False:
            questions.append(
                f"Abzug „{part.category}“ in „{p.label}“ ({part.source}) ist keine bekannte "
                f"nicht umlagefähige Kostenart."
            )
            return None
        protocol.append(
            Finding(
                p.id,
                p.label,
                PARTLY_PASSED_ON,
                part.amount,
                f"enthält {_de(part.amount)} {part_category.label} ({part.source})",
                part_category.citation,
            )
        )
        held_back += part.amount
    if held_back > p.amount:
        questions.append(f"Die Abzüge in „{p.label}“ übersteigen den Betrag.")
        return None
    return p.amount - held_back


def _heating_shares(
    cents: int,
    case: Case,
    parties: list[_Party],
    catalogue: Catalogue,
    questions: list[str],
    assumptions: list[str],
) -> list[int] | None:
    """Split the heating cost: the base part by days, the consumption part by meter reading, and
    a reading period shared by several parties by degree days (§ 9b HeizkostenV)."""
    heating = case.heating
    assert heating is not None
    if heating.hot_water_separate:
        questions.append("Getrennt ausgewiesene Warmwasserkosten werden noch nicht unterstützt.")
        return None

    readings = sorted(heating.readings, key=lambda r: r.end)
    if not readings:
        readings_spans = [(case.period_start, case.period_end, Fraction(1))]
    else:
        if readings[-1].end != case.period_end or sum(r.share for r in readings) != 1:
            questions.append(
                "Die Verbrauchsanteile des Messdienstes decken den Abrechnungszeitraum nicht "
                "vollständig ab (Summe muss 100 % sein, letzte Ablesung zum Periodenende)."
            )
            return None
        readings_spans = []
        start = case.period_start
        for r in readings:
            readings_spans.append((start, r.end, Fraction(str(r.share))))
            start = r.end + timedelta(days=1)

    base, consumption = _split(
        cents, [Fraction(str(heating.base_share)), 1 - Fraction(str(heating.base_share))]
    )
    shares = _split(base, [Fraction(p.days) for p in parties])

    by_span = _split(consumption, [share for _, _, share in readings_spans])
    for (start, end, _), span_cents in zip(readings_spans, by_span):
        weights = []
        for party in parties:
            overlap = _overlap(party, start, end)
            weights.append(_degree_days(*overlap, catalogue) if overlap else Fraction(0))
        if sum(1 for w in weights if w) > 1:
            assumptions.append(
                f"Für {start:%d.%m.%Y}–{end:%d.%m.%Y} gibt es keine Ablesung je Nutzer; der "
                f"Verbrauch dieses Zeitraums ist nach Gradtagszahlen aufgeteilt. Der Warmwasser"
                f"anteil ist nicht gesondert ausgewiesen und wurde ebenso behandelt; ausgewiesen "
                f"wäre er nach Tagen zu verteilen (§ 9b Abs. 2 HeizkostenV)."
            )
        for i, part in enumerate(_split(span_cents, weights)):
            shares[i] += part
    return shares


def _degree_days(start: date, end: date, catalogue: Catalogue) -> Fraction:
    total = Fraction(0)
    day = start
    while day <= end:
        days_in_month = calendar.monthrange(day.year, day.month)[1]
        total += catalogue.degree_days[day.month] / days_in_month
        day += timedelta(days=1)
    return total


def _deadline(period_end: date) -> date:
    try:
        return period_end.replace(year=period_end.year + 1)
    except ValueError:  # 29 February
        return period_end.replace(year=period_end.year + 1, day=28)


def settle(case: Case, catalogue: Catalogue, policy: Policy = Policy()) -> Settlement:
    questions: list[str] = []
    assumptions: list[str] = []
    protocol: list[Finding] = []

    parties = _parties(case, questions, assumptions)
    allocable: dict[str, Decimal] = {}
    other_key: list[str] = []
    for p in case.positions:
        amount = _allocable(p, case, catalogue, protocol, questions)
        if amount is None:
            continue
        allocable[p.id] = amount
        is_heating = case.heating is not None and p.id == case.heating.position_id
        if amount and not is_heating and p.weg_key not in (case.lease.key, "direkt"):
            other_key.append(p.label)

    heating_id = case.heating.position_id if case.heating else None
    if heating_id is not None and heating_id not in {p.id for p in case.positions}:
        questions.append(f"Die Heizkostenposition „{heating_id}“ fehlt in der Abrechnung.")
    for p in case.positions:
        category = catalogue.categories.get(p.category or "")
        if category and category.split == "heizkostenv" and p.id != heating_id:
            questions.append(f"Für „{p.label}“ fehlt die Einzelabrechnung des Messdienstes.")

    if other_key:
        if policy.basis == "unit_share":
            assumptions.append(
                "Die WEG hat diese Kosten nicht nach dem im Mietvertrag vereinbarten Maßstab "
                "verteilt oder den Maßstab nicht angegeben: "
                + ", ".join(other_key)
                + ". Als Gesamtkosten der Wohnung ist der Anteil laut Hausgeldabrechnung "
                "angesetzt."
            )
        else:
            questions.append(
                "Der Mietvertrag verteilt nach Wohnfläche, die Hausgeldabrechnung nennt für "
                + ", ".join(other_key)
                + " keinen oder einen anderen Verteilungsschlüssel. Bitte die Gesamtabrechnung "
                "der WEG (Gesamtkosten je Position) nachreichen oder bestätigen, dass der Anteil "
                "der Wohnung laut Hausgeldabrechnung als Gesamtkosten angesetzt wird."
            )

    advances: dict[str, Decimal] = {}
    for t in case.tenancies:
        span = _in_period(case, t)
        if span is None:
            continue  # already asked about
        if t.advances_paid is not None:
            advances[t.tenant] = t.advances_paid
            continue
        months = _whole_months(*span)
        if months is None:
            questions.append(
                f"Die Mietzeit von {t.tenant} beginnt oder endet im laufenden Monat; bitte die "
                f"tatsächlich geleisteten Vorauszahlungen angeben."
            )
            continue
        advances[t.tenant] = t.monthly_advance * months
        assumptions.append(
            f"Vorauszahlungen von {t.tenant} als vollständig geleistet angesetzt "
            f"({months} × {_de(t.monthly_advance)}); bitte die Zahlungseingänge bestätigen."
        )

    deadline = _deadline(case.period_end)
    if questions:
        return Settlement((), tuple(protocol), Decimal("0.00"), None, deadline,
                          tuple(assumptions), tuple(questions))

    period_days = sum(party.days for party in parties)
    shares: dict[str, list[int]] = {}
    keys: dict[str, list[str]] = {}
    for p in case.positions:
        cents = _cents(allocable[p.id])
        if p.id == heating_id:
            heating = _heating_shares(cents, case, parties, catalogue, questions, assumptions)
            if heating is None:
                return Settlement((), tuple(protocol), Decimal("0.00"), None, deadline,
                                  tuple(assumptions), tuple(questions))
            shares[p.id] = heating
            keys[p.id] = ["HeizkostenV"] * len(parties)
            if len(parties) > 1:
                protocol.append(
                    Finding(
                        p.id,
                        p.label,
                        SPECIAL_KEY,
                        allocable[p.id],
                        f"Grundkosten ({case.heating.base_share:.0%}) nach Tagen, Verbrauchs"
                        f"kosten nach den Ablesungen des Messdienstes, Zeiträume ohne Ablesung "
                        f"nach Gradtagszahlen",
                        _HEATING_CHANGE_CITATION,
                    )
                )
        else:
            shares[p.id] = _split(cents, [Fraction(party.days) for party in parties])
            keys[p.id] = [f"{party.days}/{period_days} Tage" for party in parties]

    if any(party.name == VACANCY for party in parties):
        protocol.append(
            Finding(
                "*",
                "Leerstand",
                SPECIAL_KEY,
                Decimal("0.00"),
                "Der auf leerstehende Zeiträume entfallende Anteil trägt der Vermieter.",
                _VACANCY_CITATION,
            )
        )

    statements = []
    for i, party in enumerate(parties):
        lines = tuple(
            Line(p.id, p.label, p.amount, allocable[p.id], _euros(shares[p.id][i]), keys[p.id][i])
            for p in case.positions
        )
        total = sum((line.share for line in lines), Decimal("0.00"))
        paid = advances.get(party.name, Decimal("0.00"))
        statements.append(
            Statement(party.name, party.start, party.end, lines, total, paid, total - paid)
        )

    allocable_total = sum(allocable.values(), Decimal("0.00"))
    return Settlement(
        statements=tuple(statements),
        protocol=tuple(protocol),
        allocable_total=allocable_total,
        proposal=_proposal(case, allocable_total, parties),
        deadline=deadline,
        assumptions=tuple(assumptions),
    )


def _proposal(case: Case, allocable_total: Decimal, parties: list[_Party]) -> Proposal | None:
    """§ 560 Abs. 4 BGB: the new monthly advance for whoever rents the unit at the period's end,
    from the whole year's cost of the unit."""
    current = next(
        (t for t in case.tenancies if (_in_period(case, t) or (None, None))[1] == case.period_end),
        None,
    )
    if current is None:
        return None
    # The months this settlement covers, not always twelve (red flag C6): dividing a half
    # year's costs by 12 would propose half the advance the unit needs.
    months = _whole_months(case.period_start, case.period_end)
    if months is None:
        days = (case.period_end - case.period_start).days + 1
        proposed = (allocable_total * 365 / days / 12).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        reasoning = (
            f"Umlagefähige Kosten der Wohnung für {days} Tage {_de(allocable_total)}, auf ein "
            f"Jahr gerechnet ÷ 12 Monate = {_de(proposed)} (bisher {_de(current.monthly_advance)})."
        )
    else:
        proposed = (allocable_total / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        reasoning = (
            f"Umlagefähige Kosten der Wohnung für den Abrechnungszeitraum "
            f"{_de(allocable_total)} ÷ {months} Monate = {_de(proposed)} "
            f"(bisher {_de(current.monthly_advance)})."
        )
    if len(parties) > 1:
        reasoning += (
            " Bezugsbasis sind die Kosten des ganzen Jahres, nicht die Monate dieses Mieters "
            "hochgerechnet: die Heizkosten verteilen sich ungleich über das Jahr, ein Teiljahr "
            "würde den Bedarf verzerren."
        )
    return Proposal(current.tenant, current.monthly_advance, allocable_total, proposed, reasoning)
