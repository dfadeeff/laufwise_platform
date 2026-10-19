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
    "Tenancy.start": "Beginn laut Dokument (leer: nicht angegeben)",
    "Tenancy.end": "Ende laut Dokument (leer: läuft weiter)",
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

GUIDE = """
The documents are those of one Betriebskostenabrechnung for one rented flat: the owners'
association's Hausgeldabrechnung for the flat (Unterlage 1), and usually a Grundsteuerbescheid, a
heating bill from the Messdienst and an extract of the lease.

- positions: one per cost row of the Hausgeldabrechnung, in its order; never the total row. Add a
  position for every cost billed to the flat in a separate document (e.g. the Grundsteuer), with
  weg_key "direkt". id: "U<document number>-<row number, two digits>", e.g. U1-01, U2-01.
  label: the row's text without remarks in brackets. source: the document, e.g. "Unterlage 1".
- category: the catalogue key whose label matches the row; null when unsure. Never decide here
  whether a cost is passed on to the tenant: that is computed later.
- excluded: when a row says it contains an amount for something else (e.g. "darin enthalten
  78,00 € für Kleinreparaturen"), record that amount with its category; the row's amount stays
  the full amount.
- weg_key: only when the document says how the association split that row ("Umlage nach
  Fläche" = flaeche, a Messdienst bill or consumption = verbrauch); otherwise null.
- tenancies: every tenant who rented the flat during the period. start and end exactly as the
  documents state them, even when they lie outside the period (a Mietbeginn in 2021 stays
  2021-03-01); null when a document does not state one (a tenancy that continues has no end).
  The billing period is never a tenancy date. monthly_advance as stated. advances_paid only when
  the documents state what was actually paid; otherwise null. A vacant month is not a tenancy.
- lease.key: "flaeche" when the lease splits by Wohnfläche, "mea" by Miteigentumsanteile.
  named_other_costs: only costs the lease names explicitly; a general reference to § 2 BetrKV
  names none.
- heating: when there is a heating row. position_id: that row's id. base_share: the
  Grundkosten share (40 % -> 0.40; "50/50 Verbrauch/Fläche" -> 0.50). readings: one per meter
  reading the heating bill states, with the share of consumption up to it, in date order, the
  last one at the end of the period. No readings when the bill states none.
"""

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
    extraction_guide=GUIDE,
)
