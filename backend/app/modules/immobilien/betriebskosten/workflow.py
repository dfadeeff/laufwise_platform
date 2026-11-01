"""The Betriebskostenabrechnung as a workflow a workspace can create agents from (ADR-0030).

The kernel stores the case, renders its forms from `Case`, `Policy` and `Settlement`, and always
runs `verify` on a result. This file only adapts: raw input to a `Case`, and the German labels a
property manager reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import InvalidOperation

from app.kernel import Computed, Draft, InputError, Workflow, plain
from app.modules.immobilien.betriebskosten.calculate import load_catalogue, settle
from app.modules.immobilien.betriebskosten.model import (
    VACANCY,
    Case,
    CaseError,
    Policy,
    Settlement,
    parse_case,
)
from app.modules.immobilien.betriebskosten.verify import verify
from app.modules.immobilien.letters import euro as _euro, salutation as _salutation

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
    except CaseError as exc:
        raise InputError(exc.messages) from exc
    except KeyError as exc:
        raise InputError([f"Es fehlt das Feld „{exc.args[0]}“."]) from exc
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise InputError([f"Ein Wert ist ungültig (Datum als JJJJ-MM-TT, Beträge als 12.34): {exc}"]) from exc
    policy = Policy(
        basis=settings.get("basis") or None,
        sender=(settings.get("sender") or "").strip(),
        sender_address=(settings.get("sender_address") or "").strip(),
        payment_days=int(settings.get("payment_days") or 30),
    )
    return _Parsed(case, policy)


def _compute(parsed: _Parsed) -> Computed:
    settlement = settle(parsed.case, CATALOGUE, parsed.policy)
    result = None
    if settlement.ready:
        result = {k: v for k, v in plain(settlement).items() if k not in ("questions", "assumptions")}
    return Computed(settlement, result, settlement.questions, settlement.assumptions)


def _check(parsed: _Parsed, settlement: Settlement) -> list[str]:
    return verify(parsed.case, settlement, CATALOGUE)


def _enclosure(case: Case, settlement: Settlement, s) -> str:
    """The itemised statement for one party, as plain text: every position with what was passed
    on and this party's share, the totals, and what was not passed on and why."""
    width = max(len(line.label) for line in s.lines)
    rows = [
        f"Betriebskostenabrechnung {case.period_end.year} – {case.unit}",
        f"{s.party}, {s.start:%d.%m.%Y} – {s.end:%d.%m.%Y}",
        f"Abrechnungsfrist nach § 556 Abs. 3 BGB: {settlement.deadline:%d.%m.%Y}",
        "",
        f"{'Position'.ljust(width)}  {'umlagefähig':>14}  {'Ihr Anteil':>12}  Maßstab",
    ]
    for line in s.lines:
        rows.append(
            f"{line.label.ljust(width)}  {_euro(line.allocable):>14}  {_euro(line.share):>12}  "
            f"{line.key}"
        )
    if s.final:
        kind = "Nachzahlung" if s.balance > 0 else "Guthaben" if s.balance < 0 else "ausgeglichen"
        rows += [
            "",
            f"{'Summe Ihres Anteils'.ljust(width)}  {'':>14}  {_euro(s.total):>12}",
            f"{'Ihre Vorauszahlungen'.ljust(width)}  {'':>14}  {_euro(s.advances):>12}",
            f"{f'Saldo ({kind})'.ljust(width)}  {'':>14}  {_euro(abs(s.balance)):>12}",
        ]
    else:
        rows += [
            "",
            f"{'Summe der feststehenden Positionen'.ljust(width)}  {'':>14}  {_euro(s.total):>12}",
            f"{'Ihre Vorauszahlungen'.ljust(width)}  {'':>14}  {_euro(s.advances):>12}",
            f"{'Saldo'.ljust(width)}  {'':>14}  {'folgt':>12}",
            "",
            "Noch zu verteilen:",
        ]
        rows += [
            f"- {o.what}, nach {o.method}. Beim {o.addressee} angefordert bis {o.due:%d.%m.%Y}."
            for o in _open_for(settlement, s)
        ]
    rows += ["", "Nicht oder nur teilweise umgelegt, oder nach besonderem Maßstab:"]
    rows += [f"- {f.label}: {f.treatment}. {f.reason} ({f.citation})" for f in settlement.protocol]
    return "\n".join(rows) + "\n"


def _open_for(settlement: Settlement, s) -> list:
    return [o for o in settlement.open_items if s.start <= o.end and o.start <= s.end]


def _closing(policy: Policy) -> str:
    return "Mit freundlichen Grüßen" + (f"\n{policy.sender}" if policy.sender else "")


def _pending(case: Case, settlement: Settlement, policy: Policy, s) -> Draft:
    """A tenant whose heating share waits on the Messdienst: what is open, who supplies it, and
    the date by which the complete settlement arrives. No balance, so nothing to pay on a figure
    that may still change, and no letter without a date."""
    (o, *_) = _open_for(settlement, s)
    sentences = [
        f"Ihre Betriebskostenabrechnung für den Zeitraum {s.start:%d.%m.%Y} bis "
        f"{s.end:%d.%m.%Y} steht bis auf eine Position fest: den Verbrauchsanteil der Heiz- und "
        f"Warmwasserkosten für {o.start:%d.%m.%Y} bis {o.end:%d.%m.%Y}.",
        f"Für dessen Verteilung nach § 9b HeizkostenV benötigen wir vom {o.addressee} die "
        f"Trennung in Heizung und Warmwasser, die wir bis zum {o.due:%d.%m.%Y} angefordert haben.",
        f"Die vollständige Abrechnung erhalten Sie spätestens bis zum {settlement.deadline:%d.%m.%Y}, "
        f"dem Ende der Abrechnungsfrist nach § 556 Abs. 3 BGB.",
        f"Ihre Vorauszahlungen von {_euro(s.advances)} sind darin berücksichtigt.",
        "Die Aufstellung der feststehenden Positionen liegt bei.",
    ]
    return Draft(
        title=f"Betriebskostenabrechnung {case.period_end.year} – {case.unit}: Zwischenstand",
        recipient=s.party,
        text=f"{_salutation(s.party)}\n\n{' '.join(sentences)}\n\n{_closing(policy)}\n",
        enclosure=_enclosure(case, settlement, s),
        enclosure_name=f"Aufstellung {s.party}",
        sender=policy.sender,
        sender_address=policy.sender_address,
    )


def _request(case: Case, settlement: Settlement, policy: Policy, o) -> Draft:
    """The request for the one missing fact, to whoever holds it, with the date it is needed by."""
    sentences = [
        f"für {case.unit} weist Ihre Abrechnung {case.period_end.year} für den Zeitraum "
        f"{o.start:%d.%m.%Y} bis {o.end:%d.%m.%Y} Verbrauchskosten von {_euro(o.amount)} aus, "
        f"ohne Heizung und Warmwasser zu trennen.",
        "In diesem Zeitraum hat der Nutzer gewechselt, ohne dass eine Ablesung je Nutzer "
        "vorliegt.",
        "Nach § 9b Abs. 3 und 2 HeizkostenV sind die Heizkosten daher nach Gradtagszahlen, die "
        "Warmwasserkosten zeitanteilig zu verteilen.",
        f"Bitte teilen Sie uns bis zum {o.due:%d.%m.%Y} mit, welcher Teil der {_euro(o.amount)} "
        f"auf das Warmwasser entfällt.",
    ]
    return Draft(
        title=f"Heizkostenabrechnung {case.period_end.year} – {case.unit}: Trennung Heizung und Warmwasser",
        recipient=o.addressee,
        text=f"Sehr geehrte Damen und Herren,\n\n{' '.join(sentences)}\n\n{_closing(policy)}\n",
        sender=policy.sender,
        sender_address=policy.sender_address,
    )


def _drafts(parsed: _Parsed, settlement: Settlement) -> list[Draft]:
    """One letter per tenant, none for a vacant month: at most five sentences, with the costs,
    the advances, the balance and its deadline, and the proposed new advance for whoever rents
    the flat now. A tenant whose share is still open gets the date of the complete settlement
    instead, and whoever must supply the missing fact gets a request. A template, so the same
    result always gives the same letters."""
    case, policy = parsed.case, parsed.policy
    days = policy.payment_days
    period = f"{case.period_start:%d.%m.%Y} bis {case.period_end:%d.%m.%Y}"
    letters = []
    for s in settlement.statements:
        if s.party == VACANCY:
            continue
        if not s.final:
            letters.append(_pending(case, settlement, policy, s))
            continue
        sentences = [
            f"anbei erhalten Sie die Betriebskostenabrechnung für den Zeitraum {period}.",
            f"Auf Sie entfallen Betriebskosten von {_euro(s.total)}; Ihre Vorauszahlungen "
            f"betrugen {_euro(s.advances)}.",
        ]
        if s.balance > 0:
            sentences.append(
                f"Daraus ergibt sich eine Nachzahlung von {_euro(s.balance)}, die wir Sie "
                f"bitten, innerhalb von {days} Tagen nach Zugang dieses Schreibens zu überweisen."
            )
        elif s.balance < 0:
            sentences.append(
                f"Daraus ergibt sich ein Guthaben von {_euro(-s.balance)}, das wir Ihnen "
                f"innerhalb von {days} Tagen nach Zugang dieses Schreibens erstatten."
            )
        else:
            sentences.append("Ihre Vorauszahlungen decken die Kosten genau.")
        proposal = settlement.proposal
        if proposal and proposal.tenant == s.party and proposal.proposed != proposal.current:
            sentences.append(
                f"Wir schlagen vor, Ihre monatliche Vorauszahlung nach § 560 Abs. 4 BGB von "
                f"{_euro(proposal.current)} auf {_euro(proposal.proposed)} anzupassen."
            )
        sentences.append("Die Einzelaufstellung und das Prüfprotokoll liegen bei.")
        closing = _closing(policy)
        letters.append(
            Draft(
                title=f"Betriebskostenabrechnung {case.period_end.year} – {case.unit}",
                recipient=s.party,
                text=f"{_salutation(s.party)}\n\n{' '.join(sentences)}\n\n{closing}\n",
                enclosure=_enclosure(case, settlement, s),
                enclosure_name=f"Einzelaufstellung {s.party}",
                sender=policy.sender,
                sender_address=policy.sender_address,
            )
        )
    letters += [_request(case, settlement, policy, o) for o in settlement.open_items]
    return letters


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
  last one at the end of the period. No readings when the bill states none. hot_water_share:
  only when the bill states which part of the consumption costs of a period without a reading
  per tenant is hot water (30 % -> 0.30); otherwise null.
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
    drafts=_drafts,
)
