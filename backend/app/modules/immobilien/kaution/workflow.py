"""The Kautionsabrechnung as a workflow of the `immobilien` module (ADR-0036).

Everything around it is the kernel's, unchanged: the case, the forms rendered from these
dataclasses, reading documents into the input, the check run on every result, the letter's
amount check and the approval gate that sends it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.kernel import Computed, Draft, InputError, Workflow, plain
from app.modules.immobilien.kaution.calculate import load_catalogue, settle
from app.modules.immobilien.kaution.model import (
    NOT_CHARGED,
    Deposit,
    DepositError,
    DepositSettlement,
    Policy,
    parse_deposit,
)
from app.modules.immobilien.kaution.verify import verify
from app.modules.immobilien.letters import euro, salutation

CATALOGUE = load_catalogue()


@dataclass(frozen=True)
class _Parsed:
    deposit: Deposit
    policy: Policy


def _parse(raw: dict, settings: dict) -> _Parsed:
    missing = [label for key, label in (("tenant", "Mieter"), ("agreed", "vereinbarte Kaution"),
                                        ("lease_end", "Ende des Mietverhältnisses")) if not raw.get(key)]
    if missing:
        raise InputError([f"Es fehlt: {', '.join(missing)}."])
    try:
        deposit = parse_deposit(raw)
    except DepositError as exc:
        raise InputError(exc.messages) from exc
    except KeyError as exc:
        raise InputError([f"Es fehlt das Feld „{exc.args[0]}“."]) from exc
    except (ValueError, TypeError) as exc:
        raise InputError([f"Ein Wert ist ungültig (Datum als JJJJ-MM-TT, Beträge als 12.34): {exc}"]) from exc
    policy = Policy(
        sender=(settings.get("sender") or "").strip(),
        payment_days=int(settings.get("payment_days") or 30),
    )
    return _Parsed(deposit, policy)


def _compute(parsed: _Parsed) -> Computed:
    s = settle(parsed.deposit, CATALOGUE)
    result = None
    if s.ready:
        result = {k: v for k, v in plain(s).items() if k not in ("questions", "assumptions")}
    return Computed(s, result, s.questions, s.assumptions)


def _check(parsed: _Parsed, s: DepositSettlement) -> list[str]:
    return verify(parsed.deposit, s, CATALOGUE)


def _enclosure(s: DepositSettlement) -> str:
    width = max([len(i.label) for i in s.items] + [24])
    rows = [
        f"Kautionsabrechnung – {s.unit}",
        s.tenant,
        "",
        f"{'Gezahlte Kaution'.ljust(width)}  {euro(s.paid):>12}",
        f"{'Zinsen'.ljust(width)}  {euro(s.interest):>12}",
        f"{'Einbehaltene Steuer'.ljust(width)}  {euro(s.tax_withheld):>12}",
        f"{'Ihr Guthaben'.ljust(width)}  {euro(s.credit):>12}",
        "",
    ]
    for i in s.items:
        rows.append(f"{i.label.ljust(width)}  {euro(i.amount):>12}  {i.treatment}")
        rows.append(f"{''.ljust(width)}  {i.reason} ({i.citation})")
    rows += [
        "",
        f"{'Verrechnet'.ljust(width)}  {euro(s.offset):>12}",
        f"{'Einbehalten'.ljust(width)}  {euro(s.retained):>12}",
        f"{'Auszahlung an Sie'.ljust(width)}  {euro(s.payout):>12}",
        f"{'Verbleibende Forderung'.ljust(width)}  {euro(s.remaining_claim):>12}",
    ]
    return "\n".join(rows) + "\n"


def _drafts(parsed: _Parsed, s: DepositSettlement) -> list[Draft]:
    """One letter to the tenant, from the result: what was paid and credited, what is charged
    or held back, and what is paid out or still owed, with its deadline."""
    days = parsed.policy.payment_days
    tax = f" abzüglich {euro(s.tax_withheld)} einbehaltener Steuer" if s.tax_withheld else ""
    sentences = [
        f"wir rechnen Ihre Mietkaution für {s.unit} ab.",
        f"Sie haben {euro(s.paid)} gezahlt; mit Zinsen von {euro(s.interest)}{tax} steht Ihnen "
        f"ein Guthaben von {euro(s.credit)} zu.",
    ]
    if s.offset:
        sentences.append(f"Damit verrechnen wir Forderungen von {euro(s.offset)}.")
    if s.retained:
        sentences.append(
            f"Einen Betrag von {euro(s.retained)} behalten wir bis zur ausstehenden "
            f"Betriebskostenabrechnung ein und rechnen ihn danach ab."
        )
    if s.payout:
        sentences.append(f"Den Betrag von {euro(s.payout)} überweisen wir Ihnen innerhalb von {days} Tagen.")
    elif s.remaining_claim:
        sentences.append(
            f"Es verbleibt eine Forderung von {euro(s.remaining_claim)}, die wir Sie bitten, "
            f"innerhalb von {days} Tagen zu überweisen."
        )
    else:
        sentences.append("Damit ist die Kaution vollständig verrechnet.")
    if any(i.treatment == NOT_CHARGED for i in s.items):
        sentences.append("Welche Forderungen wir nicht verrechnen und warum, steht in der beiliegenden Aufstellung.")
    else:
        sentences.append("Die Aufstellung liegt bei.")
    sender = parsed.policy.sender
    closing = "Mit freundlichen Grüßen" + (f"\n{sender}" if sender else "")
    return [
        Draft(
            title=f"Kautionsabrechnung – {s.unit}",
            recipient=s.tenant,
            text=f"{salutation(s.tenant)}\n\n{' '.join(sentences)}\n\n{closing}\n",
            enclosure=_enclosure(s),
            enclosure_name=f"Kautionsabrechnung {s.tenant}.txt",
        )
    ]


GUIDE = """
The documents are those of one Kautionsabrechnung at the end of a tenancy: the lease (its deposit
agreement and rent), proof of the deposit payments, the deposit account's statement (interest,
tax withheld), the handover record, and the invoices or quotes behind the landlord's claims.

- tenant, unit: as the lease names them. lease_end: the end of the tenancy. returned_on: the date
  the flat was handed back, from the handover record; null if no document states it.
- monthly_rent_net: the Nettokaltmiete as the lease states it. agreed: the deposit the lease agrees.
- payments: one per deposit payment the documents show, with its date and source.
- interest and tax_withheld: exactly as the deposit account states them; 0 when it states none.
- claims: one per claim the landlord makes. category: the catalogue key whose label matches;
  normal wear and tear is "abnutzung"; null when unsure. Never decide here whether a claim may be
  charged: that is computed later. evidence: the document that proves it (an invoice, a quote, an
  entry in the handover record), named; empty when there is none.
- renovation_clause: always null. Whether the lease's cosmetic-repair clause is valid is a legal
  judgment a person makes, not something to read.
"""

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

WORKFLOW = Workflow(
    key="kaution",
    label="Kautionsabrechnung",
    summary="Die Abrechnung der Mietkaution am Ende eines Mietverhältnisses: Guthaben mit Zinsen, "
    "nur nachgewiesene und zulässige Forderungen, jede mit Fundstelle.",
    input_type=Deposit,
    settings_type=Policy,
    result_type=DepositSettlement,
    parse=_parse,
    compute=_compute,
    check=_check,
    rules_version=f"kaution catalogue v{CATALOGUE.version}",
    labels=LABELS,
    choices=CHOICES,
    result_omit=("questions", "assumptions"),
    extraction_guide=GUIDE,
    drafts=_drafts,
)
