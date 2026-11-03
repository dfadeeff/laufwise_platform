"""The forms of the Kautionsabrechnung, as `scripts/write_forms.py` writes them to `forms/*.json`
(ADR-0039 D2). Read on the host when the forms are written, never at run time.
"""

from pathlib import Path

from kaution.host import load_catalogue
from kaution.model import Deposit, DepositSettlement, Policy

CATALOGUE = load_catalogue(Path(__file__).parents[1] / "rules" / "catalogue.yaml")

LABELS = {
    "Deposit.tenant": "Mieter",
    "Deposit.unit": "Wohnung",
    "Deposit.lease_end": "Ende des Mietverhältnisses",
    "Deposit.returned_on": "Rückgabe der Wohnung",
    "Deposit.monthly_rent_net": "Nettokaltmiete (€)",
    "Deposit.agreed": "Vereinbarte Kaution (€)",
    "Deposit.payments": "Kautionszahlungen",
    "Deposit.interest": "Zinsen laut Kautionskonto (€)",
    "Deposit.tax_withheld": "Einbehaltene Steuer (€)",
    "Deposit.renovation_clause": "Schönheitsreparatur-Klausel (von einer Person beurteilt)",
    "Deposit.claims": "Forderungen des Vermieters",
    "Payment.date": "Datum",
    "Payment.amount": "Betrag (€)",
    "Payment.source": "Nachweis",
    "Claim.id": "Nr.",
    "Claim.label": "Bezeichnung",
    "Claim.amount": "Betrag (€)",
    "Claim.category": "Art",
    "Claim.source": "Fundstelle",
    "Claim.evidence": "Nachweis (Rechnung, Angebot, Protokoll)",
    "Policy.sender": "Absender der Anschreiben",
    "Policy.sender_address": "Anschrift des Absenders (eine Zeile)",
    "Policy.payment_days": "Zahlungs- und Auszahlungsfrist (Tage)",
    "DepositSettlement.tenant": "Mieter",
    "DepositSettlement.unit": "Wohnung",
    "DepositSettlement.paid": "Gezahlte Kaution (€)",
    "DepositSettlement.interest": "Zinsen (€)",
    "DepositSettlement.tax_withheld": "Einbehaltene Steuer (€)",
    "DepositSettlement.credit": "Guthaben des Mieters (€)",
    "DepositSettlement.items": "Forderungen",
    "DepositSettlement.offset": "Verrechnet (€)",
    "DepositSettlement.retained": "Einbehalten (€)",
    "DepositSettlement.payout": "Auszahlung (€)",
    "DepositSettlement.remaining_claim": "Verbleibende Forderung (€)",
    "DepositSettlement.deadline": "Orientierung: Abrechnung bis (6 Monate nach Rückgabe)",
    "Item.claim_id": "Nr.",
    "Item.label": "Forderung",
    "Item.amount": "Betrag (€)",
    "Item.treatment": "Behandlung",
    "Item.reason": "Begründung",
    "Item.citation": "Fundstelle",
}

CHOICES = {
    "Claim.category": tuple((k, c.label) for k, c in CATALOGUE.categories.items()),
    "Deposit.renovation_clause": (("wirksam", "wirksam"), ("unwirksam", "unwirksam")),
}

FORMS = {
    "input": Deposit,
    "settings": Policy,
    "result": DepositSettlement,
    "labels": LABELS,
    "choices": CHOICES,
    "result_omit": ("questions", "assumptions", "decide"),
}
