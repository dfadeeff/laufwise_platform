"""Release A compatibility proof for the production agent taxonomy and calendar v3."""

from pathlib import Path

from app.templates.loader import load_template
from app.templates.taxonomy import category_for, driver_for
from app.templates.validation import validate_for_publish

RUNBOOKS = Path(__file__).parents[1] / "runbooks"


def test_existing_agent_classes_have_additive_product_views() -> None:
    assert category_for("workflow") == "operational"
    assert driver_for("workflow") == "workflow"
    assert category_for("conversational") == "conversational"
    assert driver_for("conversational") == "conversation"


def test_calendar_import_v3_remains_byte_compatible_and_publishable() -> None:
    contract = load_template(RUNBOOKS / "calendar_import.yaml")

    assert contract.name == "calendar_import"
    assert contract.version == 3
    assert contract.agent_class == "workflow"
    assert [step.id for step in contract.steps] == ["ensure_patient", "copy_appointment"]
    assert validate_for_publish(contract) == []


def test_praxis_v1_remains_conversational_without_contract_migration() -> None:
    contract = load_template(RUNBOOKS / "praxis_appointment.yaml")

    assert contract.version == 1
    assert contract.agent_class == "conversational"
    assert category_for(contract.agent_class) == "conversational"
