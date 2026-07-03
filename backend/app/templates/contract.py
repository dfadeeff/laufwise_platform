"""The template contract schema (ADR-0002 "Resulting shape of a template").

Pydantic v2 models the Studio authors and the compiler reads. Mirrors laufwise's check/binding
shape where they overlap, but is *platform-owned*: it carries the Studio-only fields the engine
must not see. In Stage 2 these models serialize to the `Template.contract` / `Template.parameters`
JSONB columns; in Stage 3 the step-form editor produces them and the publish gate validates them.

Validation of *publishability* (every enforced step has an allowlist + a verifiable condition,
etc.) is deliberately NOT here — it belongs to the Stage 3 publish gate. This file is schema only.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StepKind = Literal["trace", "enforced"]
AgentClass = Literal["conversational", "workflow"]


class CheckDef(BaseModel):
    """A pre/postcondition. `expr` is a pure predicate over state bindings (laufwise DSL).

    YAML form:  - check: consent.telephone_handling == true
                  else: "no consent to handle by phone"
    """

    model_config = ConfigDict(populate_by_name=True)

    expr: str = Field(alias="check")
    reason: str | None = Field(default=None, alias="else")


class StateBindingDef(BaseModel):
    """Binds a named state query to a provider. Checks may only read through these."""

    provider: str = "memory"
    query: str | None = None


class ApprovalDef(BaseModel):
    required_when: str | None = None
    prompt: str | None = None


class ExecuteDef(BaseModel):
    adapter: str = "stub"
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    # Demo-only: the state change this tool would cause, so a SimulatedAdapter can stand in for a
    # real system of record (laufwise convention). Real connectors (Stage 5) ignore `effect`;
    # postconditions re-query the real state regardless.
    effect: dict[str, Any] = Field(default_factory=dict)


class ParameterSpec(BaseModel):
    """One config-tier knob. The Studio auto-renders the configuration form from these
    (ADR-0002 #9). `type` drives the input widget; `options` enumerates an enum's choices."""

    type: Literal["text", "enum", "bool", "int"] = "text"
    options: list[Any] | None = None
    default: Any = None
    required: bool = False
    label: str | None = None


class StepDef(BaseModel):
    """One ordered step. `kind` decides whether it reaches the engine:
    - trace:    a conversational-surface marker (clarify/greet) — never compiled to the engine.
    - enforced: a governed action — precondition -> allowlist -> approval -> execute -> postcondition.

    `agent` (the per-step executor) is only meaningful for the *workflow* class on enforced steps
    (ADR-0002 #10); for the conversational class it stays None.
    """

    id: str
    kind: StepKind
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    preconditions: list[CheckDef] = Field(default_factory=list)
    postconditions: list[CheckDef] = Field(default_factory=list)
    approval: ApprovalDef | None = None
    execute: ExecuteDef | None = None
    agent: str | None = None
    on_fail: str = "halt"


class TemplateContract(BaseModel):
    """A complete Studio template. Top-level `parameters`/`required_connections`/`agent_*` are
    Studio concepts; only the enforced steps + state bindings cross into the engine."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(alias="template")
    version: int = 1
    risk: str = "medium"
    agent_class: AgentClass = "conversational"
    agent_surface: str | None = None
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    required_connections: list[str] = Field(default_factory=list)
    state: dict[str, StateBindingDef] = Field(default_factory=dict)
    steps: list[StepDef]

    def resolved_params(self, param_values: dict[str, Any] | None) -> dict[str, Any]:
        """Merge declared defaults with caller-supplied values (values win)."""
        merged = {key: spec.default for key, spec in self.parameters.items()}
        if param_values:
            merged.update(param_values)
        return merged