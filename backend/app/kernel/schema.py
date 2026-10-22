"""The form schema the Studio renders, derived from a module's own dataclasses (ADR-0030).

A module never writes a schema by hand: the kernel reads the dataclass it already computes with,
so the form and the code cannot drift apart. The dialect is a deliberately small JSON-Schema
subset the Studio renders completely: objects, arrays, strings, booleans, integers, decimals
(strings with `format: decimal`, so money never passes through a float), dates and enums. A type
outside it fails when the module loads, not when someone opens the form.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

Choices = Mapping[str, tuple[tuple[str, str], ...]]


def _title(owner: str, name: str, labels: Mapping[str, str]) -> str:
    return labels.get(f"{owner}.{name}") or labels.get(name) or name.replace("_", " ").capitalize()


def _choices(owner: str, name: str, choices: Choices) -> tuple[tuple[str, str], ...] | None:
    return choices.get(f"{owner}.{name}") or choices.get(name)


def _type(tp: Any, owner: str, name: str, labels: Mapping[str, str], choices: Choices) -> dict:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        rest = [a for a in args if a is not type(None)]
        if len(rest) != 1 or len(args) != 2:
            raise TypeError(f"{owner}.{name}: only `X | None` unions are supported")
        return {**_type(rest[0], owner, name, labels, choices), "nullable": True}
    if origin in (tuple, list):
        item = args[0]
        if origin is tuple and (len(args) != 2 or args[1] is not Ellipsis):
            raise TypeError(f"{owner}.{name}: only tuple[T, ...] is supported")
        return {"type": "array", "items": _type(item, owner, name, labels, choices)}
    if origin is typing.Literal:
        return {"type": "string", "enum": [{"value": v, "label": str(v)} for v in args]}
    if dataclasses.is_dataclass(tp):
        return object_schema(tp, labels, choices)
    if tp is str:
        options = _choices(owner, name, choices)
        if options:
            return {"type": "string", "enum": [{"value": v, "label": lab} for v, lab in options]}
        return {"type": "string"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is Decimal:
        return {"type": "string", "format": "decimal"}
    if tp is date:
        return {"type": "string", "format": "date"}
    raise TypeError(f"{owner}.{name}: type {tp!r} has no form")


def object_schema(
    cls: type,
    labels: Mapping[str, str] = {},
    choices: Choices = {},
    omit: tuple[str, ...] = (),
) -> dict:
    """The schema of a dataclass, with titles from `labels` ("Type.field" first, then "field")
    and enums from `choices`, keyed the same way."""
    hints = typing.get_type_hints(cls)
    properties = {}
    for f in dataclasses.fields(cls):
        if f.name in omit:
            continue
        prop = _type(hints[f.name], cls.__name__, f.name, labels, choices)
        prop["title"] = _title(cls.__name__, f.name, labels)
        # A simple default is what a new form starts from; a collection default stays empty.
        if f.default is not dataclasses.MISSING and (
            f.default is None or isinstance(f.default, (str, int, bool, Decimal))
        ):
            prop["default"] = str(f.default) if isinstance(f.default, Decimal) else f.default
        properties[f.name] = prop
    return {"type": "object", "properties": properties}


def empty(schema: dict) -> Any:
    """The blank value a new form starts from: the field's default where it has one."""
    if "default" in schema:
        return schema["default"]
    if schema.get("nullable"):
        return None
    kind = schema["type"]
    if kind == "object":
        return {name: empty(prop) for name, prop in schema["properties"].items()}
    if kind == "array":
        return []
    if kind == "boolean":
        return False
    if kind == "integer":
        return 0
    return ""


def plain(value: Any) -> Any:
    """A dataclass value as JSON: decimals as strings (no float on the way), dates as ISO."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
