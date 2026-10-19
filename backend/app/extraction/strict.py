"""The form schema (app/kernel/schema.py) as a strict structured-output schema for a model.

Strict mode means the model must return every field, and nothing else: an absent fact is an
explicit null or empty value, never a field the model left out or invented. Decimals and dates
stay strings with a pattern, exactly as the form holds them.
"""

from __future__ import annotations

_DECIMAL = r"^(-?\d+(\.\d+)?)?$"
_DATE = r"^(\d{4}-\d{2}-\d{2})?$"


def strict(schema: dict) -> dict:
    out = _strict(schema)
    if schema.get("title"):
        out["description"] = schema["title"]
    return out


def _strict(schema: dict) -> dict:
    kind = schema["type"]
    nullable = schema.get("nullable", False)
    if kind == "object":
        props = {name: strict(prop) for name, prop in schema["properties"].items()}
        obj = {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        }
        return {"anyOf": [obj, {"type": "null"}]} if nullable else obj
    if kind == "array":
        return {"type": "array", "items": strict(schema["items"])}
    if kind == "string":
        out: dict = {"type": ["string", "null"] if nullable else "string"}
        if schema.get("enum"):
            out["enum"] = [o["value"] for o in schema["enum"]] + ([None] if nullable else [])
        elif schema.get("format") == "decimal":
            out["pattern"] = _DECIMAL
            out["description"] = "a number with a dot as decimal separator, e.g. 1250.00"
        elif schema.get("format") == "date":
            out["pattern"] = _DATE
            out["description"] = "a date as YYYY-MM-DD"
        return out
    return {"type": [kind, "null"] if nullable else kind}
