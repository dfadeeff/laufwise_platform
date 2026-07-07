"""The Stage-3 publish gate (ADR-0002 #6): an ungoverned template is unrepresentable.

`validate_for_publish` returns a list of precise, human-readable violations; publishing is
refused unless the list is empty. The rules, from the build plan's done-when:

- every `enforced` step carries at least one verifiable condition (pre or post);
- an enforced step that *acts* (declares an execute tool or an allowlist) must have a
  non-empty allowlist that contains the executed tool ("references a missing tool");
  check-only enforced steps (e.g. verify_patient) legitimately carry no tools;
- an acting step must verify its outcome: at least one postcondition;
- every check expression reads through a *declared* state binding;
- every `{{param}}` placeholder resolves to a declared parameter.

Trace steps are surface markers and are exempt — they never reach the engine.
"""

from __future__ import annotations

import re
from typing import Any

from app.templates.compiler import _PLACEHOLDER
from app.templates.contract import StepDef, TemplateContract

# The leading `binding.` token of a laufwise check expression ("consent.telephone_handling == true").
_BINDING_REF = re.compile(r"^\s*(\w+)\.")


def validate_for_publish(contract: TemplateContract) -> list[str]:
    """All publishability violations, empty when the template is safe to publish."""
    errors: list[str] = []

    if not contract.steps:
        errors.append("template has no steps")

    seen_ids: set[str] = set()
    for step in contract.steps:
        if step.id in seen_ids:
            errors.append(f"duplicate step id '{step.id}'")
        seen_ids.add(step.id)
        if step.kind == "enforced":
            errors.extend(_validate_enforced_step(step, contract))

    errors.extend(_validate_placeholders(contract))
    return errors


def _validate_enforced_step(step: StepDef, contract: TemplateContract) -> list[str]:
    errors: list[str] = []
    prefix = f"enforced step '{step.id}'"

    conditions = step.preconditions + step.postconditions
    if not conditions:
        errors.append(f"{prefix}: no verifiable condition — add a pre- or postcondition")

    acts = step.execute is not None and step.execute.tool is not None
    if acts:
        if not step.tools:
            errors.append(f"{prefix}: executes a tool but has no tool allowlist")
        elif step.execute.tool not in step.tools:  # type: ignore[union-attr]
            errors.append(
                f"{prefix}: executes '{step.execute.tool}' which is missing from its "  # type: ignore[union-attr]
                f"allowlist {step.tools}"
            )
        if not step.postconditions:
            errors.append(
                f"{prefix}: acts on the system of record but has no postcondition to "
                "verify the outcome"
            )
    elif step.tools:
        # An allowlist with nothing to execute is inert, but not unsafe — allowed.
        pass

    for check in conditions:
        m = _BINDING_REF.match(check.expr)
        if m is None:
            errors.append(f"{prefix}: check '{check.expr}' is not a binding.field expression")
        elif m.group(1) not in contract.state:
            errors.append(
                f"{prefix}: check '{check.expr}' reads binding '{m.group(1)}' which is not "
                f"declared in state {sorted(contract.state)}"
            )
    return errors


def _validate_placeholders(contract: TemplateContract) -> list[str]:
    """Every {{param}} anywhere in the contract must be a declared parameter."""
    errors: list[str] = []
    declared = set(contract.parameters)
    for name in sorted(_collect_placeholders(contract.model_dump(mode="json"))):
        if name not in declared:
            errors.append(
                f"placeholder '{{{{{name}}}}}' is not a declared parameter "
                f"(declared: {sorted(declared)})"
            )
    return errors


def _collect_placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return {m.group(1) for m in _PLACEHOLDER.finditer(value)}
    if isinstance(value, dict):
        return set().union(*(_collect_placeholders(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_collect_placeholders(v) for v in value), set())
    return set()