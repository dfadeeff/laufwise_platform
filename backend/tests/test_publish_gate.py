"""Stage 3 — the publish gate makes an ungoverned template unrepresentable (pure, no DB).

Each test mutates a known-good contract in exactly one way and asserts the gate names the
violation precisely — the editor surfaces these messages verbatim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.instances.deploy import validate_param_values
from app.templates.contract import TemplateContract
from app.templates.loader import load_template
from app.templates.validation import validate_for_publish

_TEMPLATE = Path(__file__).resolve().parent.parent / "runbooks" / "praxis_appointment.yaml"


def _valid() -> dict[str, Any]:
    return {
        "template": "gate_test",
        "risk": "low",
        "state": {"rec": {"provider": "memory"}},
        "parameters": {"note": {"type": "text", "default": "x"}},
        "steps": [
            {"id": "greet", "kind": "trace"},
            {
                "id": "create",
                "kind": "enforced",
                "tools": ["create_rec"],
                "preconditions": [{"check": "rec.exists == false"}],
                "execute": {"adapter": "registry", "tool": "create_rec"},
                "postconditions": [{"check": "rec.exists == true"}],
            },
        ],
    }


def _gate(contract: dict[str, Any]) -> list[str]:
    return validate_for_publish(TemplateContract.model_validate(contract))


def test_shipped_praxis_template_passes_the_gate() -> None:
    assert validate_for_publish(load_template(_TEMPLATE)) == []


def test_valid_contract_passes() -> None:
    assert _gate(_valid()) == []


def test_enforced_step_without_condition_rejected() -> None:
    c = _valid()
    c["steps"][1]["preconditions"] = []
    c["steps"][1]["postconditions"] = []
    errors = _gate(c)
    assert any("no verifiable condition" in e for e in errors)


def test_acting_step_without_allowlist_rejected() -> None:
    c = _valid()
    c["steps"][1]["tools"] = []
    errors = _gate(c)
    assert any("no tool allowlist" in e for e in errors)


def test_executed_tool_missing_from_allowlist_rejected() -> None:
    c = _valid()
    c["steps"][1]["execute"]["tool"] = "delete_everything"
    errors = _gate(c)
    assert any("missing from its allowlist" in e for e in errors)


def test_acting_step_without_postcondition_rejected() -> None:
    c = _valid()
    c["steps"][1]["postconditions"] = []
    errors = _gate(c)
    assert any("no postcondition" in e for e in errors)


def test_check_on_undeclared_binding_rejected() -> None:
    c = _valid()
    c["steps"][1]["preconditions"] = [{"check": "ghost.exists == true"}]
    errors = _gate(c)
    assert any("binding 'ghost'" in e for e in errors)


def test_undeclared_parameter_rejected() -> None:
    c = _valid()
    c["steps"][1]["description"] = "create for {{customer}}"
    errors = _gate(c)
    assert any("'{{customer}}' is not a declared parameter" in e for e in errors)


def test_check_only_enforced_step_needs_no_tools() -> None:
    # verify_patient-style steps: conditions without an action are governed and valid.
    c = _valid()
    c["steps"].insert(
        1,
        {
            "id": "verify",
            "kind": "enforced",
            "preconditions": [{"check": "rec.exists == false"}],
        },
    )
    assert _gate(c) == []


def test_trace_steps_are_exempt() -> None:
    # A bare trace step (no tools, no conditions) is a surface marker, not a governance hole.
    assert _gate(_valid()) == []


# --- Stage 4: param_values validation against the parameter schema -----------------------


def _praxis() -> TemplateContract:
    return load_template(_TEMPLATE)


def test_missing_required_param_rejected() -> None:
    errors = validate_param_values(_praxis(), {})
    assert any("required parameter 'persona' is missing" in e for e in errors)


def test_unknown_param_rejected() -> None:
    errors = validate_param_values(_praxis(), {"persona": "Dr. Test", "ghost": 1})
    assert any("unknown parameter 'ghost'" in e for e in errors)


def test_enum_out_of_options_rejected() -> None:
    errors = validate_param_values(_praxis(), {"persona": "Dr. Test", "locale": "fr"})
    assert any("must be one of" in e for e in errors)


def test_wrong_type_rejected() -> None:
    errors = validate_param_values(_praxis(), {"persona": 42})
    assert any("'persona' must be text" in e for e in errors)


def test_valid_param_values_pass() -> None:
    values = {"persona": "Dr. Test", "locale": "de", "escalate_red_flags": True}
    assert validate_param_values(_praxis(), values) == []