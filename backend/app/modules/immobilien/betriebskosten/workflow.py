"""The Betriebskostenabrechnung as a workflow a workspace can create agents from (ADR-0030).

The kernel stores the case, renders its forms from `Case`, `Policy` and `Settlement`, and always
runs `verify` on a result. This file only adapts: raw input to a `Case`, and the German labels a
property manager reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import InvalidOperation

from app.kernel import Computed, InputError, Workflow, plain
from app.modules.immobilien.betriebskosten.calculate import load_catalogue, settle
from app.modules.immobilien.betriebskosten.model import Case, Policy, Settlement, parse_case
from app.modules.immobilien.betriebskosten.verify import verify

CATALOGUE = load_catalogue()


@dataclass(frozen=True)
class _Parsed:
    case: Case
    policy: Policy


def _parse(raw: dict, settings: dict) -> _Parsed:
    missing = [
        label
        for key, label in (
            ("unit", "Einheit"),
            ("period_start", "Abrechnungszeitraum von"),
            ("period_end", "Abrechnungszeitraum bis"),
        )
        if not raw.get(key)
    ]
    if not raw.get("positions"):
        missing.append("mindestens eine Position")
    if not raw.get("tenancies"):
        missing.append("mindestens ein Mietverhältnis")
    if missing:
        raise InputError([f"Es fehlt: {', '.join(missing)}."])
    try:
        case = parse_case(raw)
    except KeyError as exc:
        raise InputError([f"Es fehlt das Feld „{exc.args[0]}“."]) from exc
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise InputError([f"Ein Wert ist ungültig (Datum als JJJJ-MM-TT, Beträge als 12.34): {exc}"]) from exc
    return _Parsed(case, Policy(basis=settings.get("basis") or None))


def _compute(parsed: _Parsed) -> Computed:
    settlement = settle(parsed.case, CATALOGUE, parsed.policy)
    result = None
    if settlement.ready:
        result = {k: v for k, v in plain(settlement).items() if k not in ("questions", "assumptions")}
    return Computed(settlement, result, settlement.questions, settlement.assumptions)


def _check(parsed: _Parsed, settlement: Settlement) -> list[str]:
    return verify(parsed.case, settlement)


_CATEGORIES = tuple(
    (key, category.label) for key, category in CATALOGUE.categories.items()
)

LABELS = {
    # input
    "Case.unit": "Einheit",
    "Case.period_start": "Abrechnungszeitraum von",
    "Case.period_end": "Abrechnungszeitraum bis",
    "Case.positions": "Positionen",
    "Case.tenancies": "Mietverhältnisse",
    "Case.lease": "Mietvertrag",
    "Case.heating": "Heizkosten (Messdienst)",
    "Position.id": "Nr.",
    "Position.label": "Bezeichnung laut Dokument",
    "Position.amount": "Betrag (€)",
    "Position.category": "Kostenart",
    "Position.source": "Fundstelle",
    "Position.weg_key": "Verteilung durch die WEG",
    "Position.excluded": "Darin enthaltene Abzüge",
    "Exclusion.amount": "Betrag (€)",
    "Exclusion.category": "Kostenart",
    "Exclusion.source": "Fundstelle",
    "Tenancy.tenant": "Mieter",
    "Tenancy.start": "Beginn",
    "Tenancy.end": "Ende",
    "Tenancy.monthly_advance": "Vorauszahlung monatlich (€)",
    "Tenancy.advances_paid": "Tatsächlich geleistet (€)",
    "Lease.key": "Umlageschlüssel laut Mietvertrag",
    "Lease.named_other_costs": "Ausdrücklich vereinbarte sonstige Betriebskosten",
    "Heating.position_id": "Nr. der Heizkostenposition",
    "Heating.base_share": "Grundkostenanteil (z. B. 0.40)",
    "Heating.readings": "Ablesungen",
    "Heating.hot_water_separate": "Warmwasser gesondert ausgewiesen",
    "Reading.end": "Ablesung zum",
    "Reading.share": "Verbrauchsanteil (z. B. 0.68)",
    # settings
    "Policy.basis": "Wenn WEG und Mietvertrag verschieden verteilen",
    # result
    "Settlement.statements": "Abrechnungen",
    "Settlement.protocol": "Prüfprotokoll",
    "Settlement.allocable_total": "Summe umlagefähig (€)",
    "Settlement.proposal": "Anpassung der Vorauszahlung (§ 560 Abs. 4 BGB)",
    "Settlement.deadline": "Abrechnungsfrist (§ 556 Abs. 3 BGB)",
    "Statement.party": "Partei",
    "Statement.start": "von",
    "Statement.end": "bis",
    "Statement.lines": "Positionen",
    "Statement.total": "Summe (€)",
    "Statement.advances": "Vorauszahlungen (€)",
    "Statement.balance": "Saldo (€, + Nachzahlung)",
    "Line.position_id": "Nr.",
    "Line.label": "Position",
    "Line.amount": "laut Hausgeld (€)",
    "Line.allocable": "umlagefähig (€)",
    "Line.share": "Anteil (€)",
    "Line.key": "Maßstab",
    "Finding.position_id": "Nr.",
    "Finding.label": "Position",
    "Finding.treatment": "Behandlung",
    "Finding.amount": "Betrag (€)",
    "Finding.reason": "Begründung",
    "Finding.citation": "Fundstelle",
    "Proposal.tenant": "Mieter",
    "Proposal.current": "bisher (€)",
    "Proposal.basis_total": "Bezugsbasis (€)",
    "Proposal.proposed": "neu (€)",
    "Proposal.reasoning": "Rechenweg",
}

CHOICES = {
    "Position.category": _CATEGORIES,
    "Exclusion.category": _CATEGORIES,
    "Position.weg_key": (
        ("mea", "nach Miteigentumsanteilen"),
        ("flaeche", "nach Wohnfläche"),
        ("verbrauch", "nach Verbrauch"),
        ("direkt", "nur dieser Einheit berechnet"),
    ),
    "Lease.key": (("flaeche", "Wohnfläche"), ("mea", "Miteigentumsanteile")),
    "Lease.named_other_costs": tuple(
        (key, c.label) for key, c in CATALOGUE.categories.items() if c.allocable == "agreement"
    ),
    "Policy.basis": (
        ("unit_share", "Anteil der Wohnung laut Hausgeldabrechnung als Gesamtkosten ansetzen"),
    ),
}

WORKFLOW = Workflow(
    key="betriebskosten",
    label="Betriebskostenabrechnung",
    summary="Die jährliche Abrechnung einer vermieteten Eigentumswohnung: aus Hausgeld"
    "abrechnung, Grundsteuer, Heizkosten und Mietvertrag, jede Zahl mit Fundstelle.",
    input_type=Case,
    settings_type=Policy,
    result_type=Settlement,
    parse=_parse,
    compute=_compute,
    check=_check,
    rules_version=f"catalogue v{CATALOGUE.version}",
    labels=LABELS,
    choices=CHOICES,
    result_omit=("questions", "assumptions"),
)
