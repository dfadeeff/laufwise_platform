"""What every `immobilien` letter writes the same way: amounts and salutations (ADR-0036).

Shared by the module's workflows, not by the kernel: a German salutation and euro notation are
this vertical's, and a second vertical may write differently.
"""

from __future__ import annotations

from decimal import Decimal


def euro(amount: Decimal) -> str:
    """An amount as a German text prints it, 1.234,56 €: every text a tenant may read uses it."""
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def salutation(name: str) -> str:
    if name.startswith("Herr "):
        return f"Sehr geehrter {name},"
    if name.startswith(("Frau ", "Familie ")):
        return f"Sehr geehrte {name},"
    return f"Guten Tag {name},"
