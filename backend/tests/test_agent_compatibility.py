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


def test_calendar_import_remains_step_compatible_and_publishable() -> None:
    """The version moved to 4 with ADR-0011 (a fourth room joined the `rooms` default). A
    published version is immutable, so a changed default IS a new version — v3 stays exactly as
    it was seeded. What must not drift is the contract itself: same steps, still publishable.
    """
    contract = load_template(RUNBOOKS / "calendar_import.yaml")

    assert contract.name == "calendar_import"
    assert contract.version == 4
    assert contract.agent_class == "workflow"
    assert [step.id for step in contract.steps] == ["ensure_patient", "copy_appointment"]
    assert validate_for_publish(contract) == []


def test_praxis_v1_remains_conversational_without_contract_migration() -> None:
    contract = load_template(RUNBOOKS / "praxis_appointment.yaml")

    assert contract.version == 1
    assert contract.agent_class == "conversational"
    assert category_for(contract.agent_class) == "conversational"
