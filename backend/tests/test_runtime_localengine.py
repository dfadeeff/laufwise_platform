"""Stage 1 — the contract governs a run through LocalEngine (pure: no DB, no HTTP).

Proves the wedge end-to-end against the in-memory provider:
- a missing-consent case BLOCKs before any tool runs, and the failing predicate is surfaced;
- a no-free-slot case BLOCKs the booking action specifically;
- a valid case books and the postcondition verifies the (simulated) calendar write;
- the down-compiler drops trace steps and substitutes parameters.
"""

from __future__ import annotations

from pathlib import Path

from app.control_plane.runner import execute_contract
from app.templates.compiler import to_runbook_spec
from app.templates.contract import StepDef, TemplateContract
from app.templates.loader import load_template

_RUNS_DIR = "./runs"
_TEMPLATE = Path(__file__).resolve().parent.parent / "runbooks" / "praxis_appointment.yaml"


def _case(*, consent: bool, free_slot: bool) -> dict:
    return {
        "patient": {"id": "kvnr-123"},
        "consent": {"telephone_handling": consent},
        "calendar": {"has_free_slot": free_slot, "booking_confirmed": False},
    }


def _exec(case: dict):
    result = execute_contract(load_template(_TEMPLATE), case, _RUNS_DIR)
    return result, {s.step_id: s for s in result.steps}


def test_blocks_without_consent() -> None:
    result, steps = _exec(_case(consent=False, free_slot=True))
    assert steps["verify_patient"].status.value == "blocked"
    assert steps["verify_patient"].expr == "consent.telephone_handling == true"
    assert "book_slot" not in steps  # never reached
    assert result.status == "blocked"


def test_blocks_booking_without_free_slot() -> None:
    _, steps = _exec(_case(consent=True, free_slot=False))
    assert steps["verify_patient"].status.value == "ok"
    assert steps["book_slot"].status.value == "blocked"
    assert steps["book_slot"].expr == "calendar.has_free_slot == true"
    assert steps["book_slot"].blocked_tool == "book_appointment"


def test_books_when_state_allows() -> None:
    result, steps = _exec(_case(consent=True, free_slot=True))
    assert steps["verify_patient"].status.value == "ok"
    # Postcondition re-queries state after the simulated write and confirms the booking.
    assert steps["book_slot"].status.value == "ok"
    assert result.status == "ok"
    assert Path(result.trace_path).exists()


# --- compiler (down-compile) -------------------------------------------------

def test_compiler_drops_trace_steps() -> None:
    spec = to_runbook_spec(load_template(_TEMPLATE))
    # greet (trace) is dropped; only the two enforced steps reach the engine.
    assert [s.id for s in spec.steps] == ["verify_patient", "book_slot"]
    assert spec.runbook == "praxis_appointment"


def test_compiler_substitutes_parameters() -> None:
    contract = TemplateContract(
        template="t",
        parameters={"room": {"type": "text", "default": "A"}},
        steps=[
            StepDef(id="m", kind="trace"),
            StepDef(id="act", kind="enforced", description="book room {{room}}"),
        ],
    )
    assert to_runbook_spec(contract).steps[0].description == "book room A"
    assert to_runbook_spec(contract, {"room": "B"}).steps[0].description == "book room B"