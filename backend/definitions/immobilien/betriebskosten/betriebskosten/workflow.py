"""The Betriebskostenabrechnung's three steps, as its functions call them (ADR-0039 D5).

`compute_step` turns the raw input into a result, `check_step` verifies a result against the
input and the rules, `letters_step` writes the letters from a checked result. Each runs in its
own sandbox call; the platform decides what happens with their output. JSON in, JSON out: the
rules come in as data, never from a file.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import InvalidOperation

from betriebskosten.calculate import catalogue_from, settle
from sdk import from_plain, plain
from betriebskosten.model import (
    VACANCY,
    Case,
    CaseError,
    Policy,
    Settlement,
    parse_case,
)
from betriebskosten.verify import verify
from shared.letters import Draft, euro as _euro, salutation as _salutation


class InputError(Exception):
    """The input cannot be computed; `messages` say what to correct."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


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


# The input field the Messdienst's figure goes into: once it is there, the open pool splits.
HOT_WATER_SHARE = "heating.hot_water_share"


def compute_step(raw: dict, settings: dict, rules: dict) -> dict:
    """The result, or what to correct in the input. Questions block the result. An open item
    is what the platform holds the case for (the wait step): one fact, from whom, by when; the
    rest of the settlement stands and its letters go out meanwhile."""
    try:
        parsed = _parse(raw, settings)
    except InputError as exc:
        return {"errors": exc.messages}
    settlement = settle(parsed.case, catalogue_from(rules), parsed.policy)
    result = None
    if settlement.ready:
        result = {k: v for k, v in plain(settlement).items() if k not in ("questions", "assumptions")}
    return {
        "result": result,
        "questions": list(settlement.questions),
        "assumptions": list(settlement.assumptions),
        "waiting_for": [
            {
                "fact": HOT_WATER_SHARE,
                "what": f"Warmwasseranteil der Verbrauchskosten {o.start:%d.%m.%Y}–{o.end:%d.%m.%Y}",
                "from": o.addressee,
                "due": o.due.isoformat(),
            }
            for o in settlement.open_items
        ],
    }


def _settlement(result: dict) -> Settlement:
    return from_plain(Settlement, {**result, "questions": [], "assumptions": []})


def check_step(raw: dict, settings: dict, rules: dict, result: dict) -> list[str]:
    """The postcondition: re-derives the result's invariants from the input and the rules."""
    return verify(_parse(raw, settings).case, _settlement(result), catalogue_from(rules))


def letters_step(raw: dict, settings: dict, rules: dict, result: dict) -> list[dict]:
    return [dataclasses.asdict(d) for d in _drafts(_parse(raw, settings), _settlement(result))]


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
        if s.payable is not None and settlement.late:
            # Billed after the deadline (§ 556 Abs. 3 Satz 3 BGB): what it comes to, line by line.
            rows += [
                "",
                f"Abrechnungsfrist nach § 556 Abs. 3 BGB abgelaufen am {settlement.late.deadline:%d.%m.%Y}.",
                f"{'Nicht mehr durchsetzbare Nachforderung'.ljust(width)}  {'':>14}  {_euro(s.lost):>12}",
                f"{'Gesondert durchsetzbar'.ljust(width)}  {'':>14}  {_euro(s.claimable):>12}",
                *[f"  {line.label}: {_euro(line.claimable)}" for line in s.lines if line.claimable],
                f"{('Zu zahlen' if s.payable > 0 else 'Zu erstatten' if s.payable < 0 else 'Ausgeglichen').ljust(width)}"
                f"  {'':>14}  {_euro(abs(s.payable)):>12}",
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
        if s.payable is not None and settlement.late:
            sentences += _late_sentences(s, settlement.late, days)
        elif s.balance > 0:
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
    if settlement.rent_increase:
        letters.append(_rent_letter(case, settlement.rent_increase, policy))
    return letters


def _late_sentences(s, late, days: int) -> list[str]:
    """A settlement after its deadline: what is not claimed and why, and what is paid or refunded,
    with its deadline (§ 556 Abs. 3 Satz 3 BGB)."""
    named = ", ".join(line.label for line in s.lines if line.claimable)
    out = [
        f"Die Abrechnungsfrist nach § 556 Abs. 3 BGB lief am {late.deadline:%d.%m.%Y} ab"
        + (f"; eine Nachforderung von {_euro(s.lost)} aus dieser Abrechnung machen wir deshalb nicht geltend." if s.lost else ".")
    ]
    if s.payable > 0:
        out.append(
            f"Bitte zahlen Sie die gesondert durchsetzbare Nachbelastung ({named}) von {_euro(s.payable)} "
            f"innerhalb von {days} Tagen nach Zugang dieses Schreibens."
        )
    elif s.payable < 0:
        out.append(
            f"Ihr Guthaben von {_euro(-s.payable)} erstatten wir Ihnen innerhalb von {days} Tagen nach Zugang dieses Schreibens"
            + (f"; die nachträglich festgesetzte Position ({named}) von {_euro(s.claimable)} ist darin verrechnet." if s.claimable else ".")
        )
    else:
        out.append(
            "Ein Betrag zu Ihren Lasten wird nicht geltend gemacht; Einwendungen gegen die Abrechnung können Sie "
            "innerhalb von zwölf Monaten nach Zugang erheben (§ 556 Abs. 3 Satz 5 BGB)."
        )
    return out


def _rent_letter(case: Case, r, policy: Policy) -> Draft:
    """The request to agree to a higher rent (§ 558a BGB): in text form, with its reasons, the
    date to agree by and the date the higher rent is owed from. A letter of its own
    (`Settlement.letter_strategy`)."""
    percent = f"{r.cap_percent.normalize():f}".replace(".", ",")
    surcharge = (
        f" Zusammen mit dem unverändert fortbestehenden Zuschlag nach § 559 BGB von {_euro(r.surcharge)} "
        f"beträgt Ihre Miete künftig {_euro(r.new_total)} statt {_euro(r.current_total)}."
        if r.surcharge else ""
    )
    sentences = [
        f"wir bitten Sie, einer Erhöhung der Nettokaltmiete nach § 558 BGB von {_euro(r.current_rent)} auf "
        f"{_euro(r.new_rent)} monatlich zuzustimmen; das sind {_euro(r.increase)} mehr.{surcharge}",
        f"Die ortsübliche Vergleichsmiete beträgt nach dem Mietspiegel {_euro(r.local_rent)}; die "
        f"Kappungsgrenze von {percent} % auf die Miete von {_euro(r.base_rent)} vor drei Jahren erlaubt "
        f"höchstens {_euro(r.cap_limit)}, sie ist hier die bindende Grenze." if r.binding == "Kappungsgrenze" else
        f"Die ortsübliche Vergleichsmiete beträgt nach dem Mietspiegel {_euro(r.local_rent)}; sie ist hier die "
        f"bindende Grenze, die Kappungsgrenze von {percent} % erlaubte {_euro(r.cap_limit)}.",
        f"Bitte erklären Sie Ihre Zustimmung in Textform bis zum {r.consent_by:%d.%m.%Y}; die erhöhte Miete "
        f"ist ab dem {r.effective:%d.%m.%Y} geschuldet.",
        "Die Begründung mit dem Mietspiegel und die Berechnung liegen bei.",
    ]
    enclosure = "\n".join([
        f"Mieterhöhungsverlangen nach § 558 BGB – {case.unit}",
        f"{r.tenant}; Zugang des Verlangens: {r.delivered:%d.%m.%Y}",
        "",
        r.reasoning,
        "",
        f"Zustimmung bis: {r.consent_by:%d.%m.%Y} (§ 558b Abs. 2 BGB)",
        f"Erhöhte Miete ab: {r.effective:%d.%m.%Y} (§ 558b Abs. 1 BGB)",
        "",
        *r.formal,
    ])
    return Draft(
        title=f"Mieterhöhungsverlangen nach § 558 BGB – {case.unit}",
        recipient=r.tenant,
        text=f"{_salutation(r.tenant)}\n\n{' '.join(sentences)}\n\n{_closing(policy)}\n",
        enclosure=enclosure,
        enclosure_name="Begründung der Mieterhöhung",
        sender=policy.sender,
        sender_address=policy.sender_address,
    )
