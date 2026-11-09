"""The forms of the Betriebskostenabrechnung, as `scripts/write_forms.py` writes them to
`forms/*.json` (ADR-0039 D2). Read on the host when the forms are written, never at run time:
the platform reads the JSON, not this file.
"""

from pathlib import Path

from betriebskosten.host import load_catalogue
from betriebskosten.model import Case, Policy, Settlement

CATALOGUE = load_catalogue(Path(__file__).parents[1] / "rules" / "catalogue.yaml")

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
    "Heating.hot_water_share": "Warmwasseranteil der Verbrauchskosten bei Nutzerwechsel ohne Ablesung (z. B. 0.30)",
    "Reading.end": "Ablesung zum",
    "Reading.share": "Verbrauchsanteil (z. B. 0.68)",
    "Case.today": "Heute (Bearbeitungstag laut Auftrag)",
    "Case.rent_increase": "Mieterhöhung nach § 558 BGB (leer: keine)",
    "RentIncrease.tenant": "Mieter",
    "RentIncrease.area_sqm": "Wohnfläche (m²)",
    "RentIncrease.local_rent_per_sqm": "Ortsübliche Vergleichsmiete laut Mietspiegel (€/m²)",
    "RentIncrease.delivered": "Zugang des Erhöhungsverlangens",
    "RentIncrease.rents": "Mietentwicklung (Nettokaltmiete)",
    "RentStep.start": "ab",
    "RentStep.rent": "Nettokaltmiete ohne Zuschläge (€)",
    "RentStep.surcharge": "Zuschlag nach §§ 559-560 BGB ab dann (€)",
    "RentStep.basis": "Grundlage",
    # settings
    "Policy.basis": "Wenn WEG und Mietvertrag verschieden verteilen",
    "Policy.sender": "Absender der Anschreiben",
    "Policy.sender_address": "Anschrift des Absenders (eine Zeile)",
    "Policy.payment_days": "Zahlungs- und Erstattungsfrist (Tage)",
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
    "Statement.final": "abschließend",
    "Settlement.open_items": "Offene Positionen",
    "Settlement.deadline_days_left": "Tage bis zur Abrechnungsfrist",
    "Settlement.rent_increase": "Mieterhöhung nach § 558 BGB",
    "Settlement.letter_strategy": "Ein Schreiben oder zwei, und warum",
    "RentIncreaseResult.tenant": "Mieter",
    "RentIncreaseResult.delivered": "Zugang",
    "RentIncreaseResult.consent_by": "Zustimmung bis (§ 558b Abs. 2 BGB)",
    "RentIncreaseResult.effective": "Erhöhte Miete ab (§ 558b Abs. 1 BGB)",
    "RentIncreaseResult.base_date": "Stichtag drei Jahre vorher",
    "RentIncreaseResult.base_rent": "Miete drei Jahre vorher (€)",
    "RentIncreaseResult.cap_percent": "Kappungsgrenze (%)",
    "RentIncreaseResult.cap_limit": "Kappungsgrenze (€)",
    "RentIncreaseResult.local_rent": "Ortsübliche Vergleichsmiete (€)",
    "RentIncreaseResult.current_rent": "Bisherige Nettokaltmiete ohne Zuschläge (€)",
    "RentIncreaseResult.surcharge": "Fortbestehender Zuschlag nach § 559 BGB (€)",
    "RentIncreaseResult.current_total": "Bisherige Miete gesamt (€)",
    "RentIncreaseResult.new_rent": "Neue Nettokaltmiete ohne Zuschläge (€)",
    "RentIncreaseResult.new_total": "Neue Miete gesamt (€)",
    "RentIncreaseResult.increase": "Erhöhung (€)",
    "RentIncreaseResult.binding": "Bindende Grenze",
    "RentIncreaseResult.unchanged_since": "Unverändert seit",
    "RentIncreaseResult.months_unchanged": "Monate unverändert bei Wirksamwerden",
    "RentIncreaseResult.formal": "Formelle Voraussetzungen",
    "RentIncreaseResult.reasoning": "Rechenweg",
    "OpenItem.position_id": "Nr.",
    "OpenItem.start": "von",
    "OpenItem.end": "bis",
    "OpenItem.amount": "noch zu verteilen (€)",
    "OpenItem.what": "Was fehlt",
    "OpenItem.addressee": "Von wem",
    "OpenItem.due": "Bis wann",
    "OpenItem.method": "Verteilung danach",
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
    "RentStep.basis": (
        ("vertrag", "Mietvertrag"),
        ("558", "Erhöhung nach § 558 BGB"),
        ("559", "Zuschlag nach § 559 BGB (Modernisierung)"),
    ),
    "Lease.named_other_costs": tuple(
        (key, c.label) for key, c in CATALOGUE.categories.items() if c.allocable == "agreement"
    ),
    "Policy.basis": (
        ("unit_share", "Anteil der Wohnung laut Hausgeldabrechnung als Gesamtkosten ansetzen"),
    ),
}

FORMS = {
    "input": Case,
    "settings": Policy,
    "result": Settlement,
    "labels": LABELS,
    "choices": CHOICES,
    "result_omit": ("questions", "assumptions"),
}
