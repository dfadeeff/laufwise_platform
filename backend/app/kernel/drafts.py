"""The check on every text a workflow writes from a result (ADR-0032).

A letter may word things as it likes, but every euro amount in it must be one the result holds.
A refund prints as a positive amount while the result holds a negative balance, so amounts are
compared without their sign. Other numbers (a paragraph, a day count, a flat number) are not
amounts and are not checked.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

_AMOUNT = re.compile(r"(?<![\d.,])((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s?€")


def _numbers(value: Any) -> set[Decimal]:
    if isinstance(value, dict):
        return {n for v in value.values() for n in _numbers(v)}
    if isinstance(value, list):
        return {n for v in value for n in _numbers(v)}
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        try:
            return {abs(Decimal(str(value)))}
        except InvalidOperation:
            return set()
    return set()


def amounts_not_in(text: str, result: Any) -> list[str]:
    """The euro amounts printed in `text` that `result` does not hold."""
    known = _numbers(result)
    return [
        f"{printed} €"
        for printed in _AMOUNT.findall(text)
        if Decimal(printed.replace(".", "").replace(",", ".")) not in known
    ]
