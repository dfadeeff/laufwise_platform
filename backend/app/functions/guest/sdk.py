"""The platform's helper library inside the sandbox (ADR-0039 D5): standard library only, and no
domain rules. A function imports it as `sdk`.

It grows with what functions share: money, dates, splitting in whole cents, German formatting.
Today it holds what the first function needs.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from typing import Any


def plain(value: Any) -> Any:
    """A dataclass value as JSON: decimals as strings (no float on the way), dates as ISO. The
    same form the kernel gives a result (`app.kernel.schema.plain`)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
