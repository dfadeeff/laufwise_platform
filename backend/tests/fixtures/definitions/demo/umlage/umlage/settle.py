"""Split costs by party: each item belongs to one party; a party pays what it owes minus what it
paid. Plain dicts in and out, amounts as decimal strings."""

from decimal import Decimal, InvalidOperation


class InputError(Exception):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


def money(value, where: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise InputError([f"{where}: „{value}“ ist kein Betrag."]) from None
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        raise InputError([f"{where}: „{value}“ ist kein Betrag mit zwei Nachkommastellen."])
    if amount < 0:
        raise InputError([f"{where}: der Betrag darf nicht negativ sein."])
    return amount


def settle(raw: dict, rules: dict, settings: dict | None = None) -> tuple[dict | None, list[str], list[str]]:
    """The result, the questions that block it, and the assumptions it states. Raises
    InputError for impossible input. An unclassified item is a question, unless the agent's
    standing decision `default_category` answers it, and then an assumption."""
    missing = [label for key, label in (("title", "Bezeichnung"),) if not raw.get(key)]
    if not raw.get("items"):
        missing.append("mindestens eine Position")
    if missing:
        raise InputError([f"Es fehlt: {', '.join(missing)}."])
    categories = rules.get("categories", {})
    default = (settings or {}).get("default_category")
    questions, assumptions, owed, paid = [], [], {}, {}
    for item in raw["items"]:
        amount = money(item.get("amount"), item.get("label", "Position"))
        if item.get("category") not in categories:
            if default in categories:
                assumptions.append(f"„{item.get('label')}“ als {categories[default]} angesetzt.")
            else:
                questions.append(f"„{item.get('label')}“ ist keiner Kategorie zugeordnet.")
        owed[item["party"]] = owed.get(item["party"], Decimal("0.00")) + amount
    for payment in raw.get("payments") or []:
        paid[payment["party"]] = paid.get(payment["party"], Decimal("0.00")) + money(payment.get("amount"), payment["party"])
    if questions:
        return None, questions, assumptions
    statements = [
        {"party": party, "total": str(total), "paid": str(paid.get(party, Decimal("0.00"))),
         "balance": str(total - paid.get(party, Decimal("0.00")))}
        for party, total in owed.items()
    ]
    total = str(sum(owed.values(), Decimal("0.00")))
    return {"title": raw["title"], "total": total, "statements": statements}, [], assumptions


def euro(amount: str) -> str:
    value = Decimal(amount)
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"
