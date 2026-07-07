"""Deploy-time validation (Stage 4): param_values must satisfy the template's parameter schema.

The configuration form is auto-rendered from `TemplateContract.parameters` (ADR-0002 #9);
this module is the server-side truth the form cannot bypass. Returns precise violations,
empty when the values are deployable.
"""

from __future__ import annotations

from typing import Any

from app.templates.contract import ParameterSpec, TemplateContract

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "text": str,
    "enum": (str, int, float, bool),
    "bool": bool,
    "int": int,
}


def validate_param_values(
    contract: TemplateContract, param_values: dict[str, Any]
) -> list[str]:
    errors: list[str] = []

    for key in sorted(param_values):
        if key not in contract.parameters:
            errors.append(
                f"unknown parameter '{key}' (declared: {sorted(contract.parameters)})"
            )

    for key, spec in contract.parameters.items():
        if key in param_values:
            errors.extend(_check_value(key, spec, param_values[key]))
        elif spec.required and spec.default is None:
            errors.append(f"required parameter '{key}' is missing")

    return errors


def _check_value(key: str, spec: ParameterSpec, value: Any) -> list[str]:
    expected = _TYPE_CHECKS[spec.type]
    # bool is a subclass of int — reject True for an int parameter explicitly.
    if spec.type == "int" and isinstance(value, bool):
        return [f"parameter '{key}' must be an int, got bool"]
    if not isinstance(value, expected):
        return [f"parameter '{key}' must be {spec.type}, got {type(value).__name__}"]
    if spec.type == "enum" and spec.options is not None and value not in spec.options:
        return [f"parameter '{key}' must be one of {spec.options}, got {value!r}"]
    return []