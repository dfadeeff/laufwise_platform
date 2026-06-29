"""Down-compile a Studio `TemplateContract` to a laufwise `RunbookSpec`.

This is the seam that keeps the engine domain-agnostic (architecture-agent invariant #2):
- Only `kind == "enforced"` steps cross into the engine; trace steps are surface markers and are
  dropped here (ADR-0002 #8).
- Studio-only fields (`agent_class`, `agent_surface`, `parameters`, `required_connections`,
  `kind`, per-step `agent`) are consumed platform-side and never appear in the `RunbookSpec`.
- `{{param}}` placeholders are substituted from the resolved parameters before the spec is built,
  so the engine sees concrete checks, never template variables.

Note on approval `required_when`: ADR-0002 templates use field expressions (e.g. "type == 'urgent'").
laufwise v0's engine only gates on `risk >= <level>`, so a field-expr `required_when` currently
falls through to "always require approval" (auto-approved by the v0 gate). Evaluating field-expr
approvals as a pure check in the dispatch layer is Stage 8; for Stage 1 the approval simply never
*blocks* the OK path, which is the behavior the Stage-1 done-when needs.
"""

from __future__ import annotations

import re
from typing import Any

from laufwise.spec.models import (
    ApprovalSpec,
    CheckSpec,
    ExecuteSpec,
    RunbookSpec,
    StateBinding,
    StepSpec,
)

from app.templates.contract import CheckDef, StepDef, TemplateContract

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _subst(value: Any, params: dict[str, Any]) -> Any:
    """Recursively replace {{key}} placeholders in strings; leave other types untouched."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _subst(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, params) for v in value]
    return value


def _checks(defs: list[CheckDef], params: dict[str, Any]) -> list[CheckSpec]:
    return [CheckSpec(expr=_subst(c.expr, params), reason=c.reason) for c in defs]


def _to_step_spec(step: StepDef, params: dict[str, Any]) -> StepSpec:
    approval = (
        ApprovalSpec(required_when=step.approval.required_when, prompt=step.approval.prompt)
        if step.approval
        else None
    )
    execute = (
        ExecuteSpec(
            adapter=step.execute.adapter,
            tool=step.execute.tool,
            args=_subst(step.execute.args, params),
            effect=_subst(step.execute.effect, params),
        )
        if step.execute
        else None
    )
    return StepSpec(
        id=step.id,
        description=_subst(step.description, params),
        preconditions=_checks(step.preconditions, params),
        tools=step.tools,
        approval=approval,
        execute=execute,
        postconditions=_checks(step.postconditions, params),
        on_fail=step.on_fail,
    )


def to_runbook_spec(
    contract: TemplateContract, param_values: dict[str, Any] | None = None
) -> RunbookSpec:
    """Compile a template + its filled parameters into an enforced-only laufwise RunbookSpec."""
    params = contract.resolved_params(param_values)
    state = {
        name: StateBinding(provider=b.provider, query=_subst(b.query, params))
        for name, b in contract.state.items()
    }
    steps = [_to_step_spec(s, params) for s in contract.steps if s.kind == "enforced"]
    return RunbookSpec(
        runbook=contract.name,
        version=contract.version,
        risk=contract.risk,
        state=state,
        steps=steps,
    )