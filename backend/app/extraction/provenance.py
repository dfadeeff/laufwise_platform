"""Which values a model read could not be found in the documents (ADR-0033).

Every decimal and date in an extracted input is looked up in the documents' own text, in the
forms German documents print them: 1.250,00 and 1250,00 for numbers, 68 % or 50/50 for shares
(0.68, 0.50), 01.08.2025 for dates. A value that is not there is listed for the person who checks
the input. A match is not proof the value is in the right field; a miss is a reason to look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\d])")
_DOT_DECIMAL = re.compile(r"(?<![\d.,])(\d+\.\d+)(?![\d.])")
_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


@dataclass
class Found:
    numbers: set[Decimal] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)


def found_in(texts: list[str]) -> Found:
    found = Found()
    for text in texts:
        for whole, cents in _NUMBER.findall(text):
            found.numbers.add(Decimal(whole.replace(".", "") + ("." + cents if cents else "")))
        for value in _DOT_DECIMAL.findall(text):
            found.numbers.add(Decimal(value))
        for day, month, year in _DATE.findall(text):
            found.dates.add(f"{year}-{month}-{day}")
    return found


def _confirmed(value: str, fmt: str, found: Found) -> bool:
    if fmt == "date":
        return value in found.dates
    try:
        number = Decimal(value)
    except InvalidOperation:
        return False
    if number == 0 or number in found.numbers:
        return True
    # A share read as 0.68 is printed as 68 % (or 50/50).
    return abs(number) <= 1 and number * 100 in found.numbers


def unconfirmed(value, schema: dict, found: Found, label: str = "") -> list[str]:
    """Labels of the decimals and dates in `value` that the documents do not contain."""
    kind = schema["type"]
    if value is None or value == "":
        return []
    if kind == "object":
        return [
            miss
            for name, prop in schema["properties"].items()
            for miss in unconfirmed(
                value.get(name), prop, found, f"{label} · {prop.get('title', name)}".strip(" ·")
            )
        ]
    if kind == "array":
        return [
            miss
            for i, item in enumerate(value)
            for miss in unconfirmed(item, schema["items"], found, f"{label} #{i + 1}")
        ]
    fmt = schema.get("format")
    if kind == "string" and fmt in ("decimal", "date") and not _confirmed(str(value), fmt, found):
        return [f"{label}: {value}"]
    return []
