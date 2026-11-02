"""What every `immobilien` letter writes the same way: its shape, amounts and salutations
(ADR-0036).

Shared by the module's workflows, not by the platform: a German salutation and euro notation are
this vertical's, and a second vertical may write differently. `Draft` is the shape the platform
expects a letters function to return (ADR-0034, ADR-0037).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Draft:
    title: str
    recipient: str
    text: str
    enclosure: str = ""
    enclosure_name: str = ""
    sender: str = ""
    sender_address: str = ""


def euro(amount: Decimal) -> str:
    """An amount as a German text prints it, 1.234,56 €: every text a tenant may read uses it."""
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def salutation(name: str) -> str:
    if name.startswith("Herr "):
        return f"Sehr geehrter {name},"
    if name.startswith(("Frau ", "Familie ")):
        return f"Sehr geehrte {name},"
    return f"Guten Tag {name},"
