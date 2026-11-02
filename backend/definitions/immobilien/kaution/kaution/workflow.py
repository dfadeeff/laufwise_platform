"""The Kautionsabrechnung's three steps, as its functions call them (ADR-0036, ADR-0039 D5).

`compute_step`, `check_step` and `letters_step` each run in their own sandbox call. JSON in, JSON
out; the rules come in as data.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from kaution.calculate import catalogue_from, settle
from sdk import from_plain, plain
from kaution.model import (
    NOT_CHARGED,
    Deposit,
    DepositError,
    DepositSettlement,
    Policy,
    parse_deposit,
)
from kaution.verify import verify
from shared.letters import Draft, euro, salutation


class InputError(Exception):
    """The input cannot be computed; `messages` say what to correct."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


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
        sender_address=(settings.get("sender_address") or "").strip(),
        payment_days=int(settings.get("payment_days") or 30),
    )
    return _Parsed(deposit, policy)


def compute_step(raw: dict, settings: dict, rules: dict) -> dict:
    """The result, or what to correct in the input. Questions block the result."""
    try:
        parsed = _parse(raw, settings)
    except InputError as exc:
        return {"errors": exc.messages}
    s = settle(parsed.deposit, catalogue_from(rules))
    result = None
    if s.ready:
        result = {k: v for k, v in plain(s).items() if k not in ("questions", "assumptions")}
    return {"result": result, "questions": list(s.questions), "assumptions": list(s.assumptions)}


def _settlement(result: dict) -> DepositSettlement:
    return from_plain(DepositSettlement, {**result, "questions": [], "assumptions": []})


def check_step(raw: dict, settings: dict, rules: dict, result: dict) -> list[str]:
    """The postcondition: re-derives the result's invariants from the input and the rules."""
    return verify(_parse(raw, settings).deposit, _settlement(result), catalogue_from(rules))


def letters_step(raw: dict, settings: dict, rules: dict, result: dict) -> list[dict]:
    return [dataclasses.asdict(d) for d in _drafts(_parse(raw, settings), _settlement(result))]


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
            enclosure_name=f"Kautionsabrechnung {s.tenant}",
            sender=parsed.policy.sender,
            sender_address=parsed.policy.sender_address,
        )
    ]
