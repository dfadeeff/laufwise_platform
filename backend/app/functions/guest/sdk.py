"""The platform's helper library inside the sandbox (ADR-0039 D5): standard library only, and no
domain rules. A function imports it as `sdk`.

It grows with what functions share: money, dates, splitting in whole cents, German formatting.
Today it holds what the first function needs.
"""

from __future__ import annotations

import dataclasses
import sys
import types
import typing
from datetime import date
from decimal import Decimal
from typing import Any


def from_plain(cls: Any, data: Any) -> Any:
    """The inverse of `plain`: a value of type `cls` from its JSON form. A function that checks a
    result, or writes letters from it, gets the result as JSON and rebuilds it with this.

    Handles dataclasses (by their type hints), `X | None`, `tuple[X, ...]`, `list[X]`,
    `dict[K, V]`, Decimal, date, and the JSON scalars."""
    if data is None:
        return None
    origin, args = typing.get_origin(cls), typing.get_args(cls)
    if origin in (typing.Union, types.UnionType):
        rest = [a for a in args if a is not type(None)]
        if len(rest) != 1:
            raise TypeError(f"only `X | None` unions are supported, not {cls}")
        return from_plain(rest[0], data)
    if origin is tuple:
        item = args[0] if args else Any
        return tuple(from_plain(item, v) for v in data)
    if origin is list:
        return [from_plain(args[0] if args else Any, v) for v in data]
    if origin is dict:
        key, value = args if args else (Any, Any)
        return {from_plain(key, k): from_plain(value, v) for k, v in data.items()}
    if dataclasses.is_dataclass(cls):
        hints = typing.get_type_hints(cls, vars(sys.modules[cls.__module__]))
        return cls(**{f.name: from_plain(hints[f.name], data[f.name]) for f in dataclasses.fields(cls) if f.name in data})
    if cls is Decimal:
        return Decimal(str(data))
    if cls is date:
        return date.fromisoformat(data)
    if cls is int and isinstance(data, str):
        return int(data)
    return data


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
