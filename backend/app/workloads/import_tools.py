"""The import write tools (ADR-0004 D6/D7, ADR-0005 D1/D2).

Two tools, one per governed step, both create-only:

- `create_patient` — find-or-create the patient card for this appointment's patient.
- `create_appointment` — append the appointment BOUND to that card.

Both re-read the appointment from the SOURCE by ref, so a stale work-list cannot cause a
fabricated write. Each tool CLAIMS success when the destination accepts it; whether anything
actually persisted is decided by the step's postcondition re-querying real state
(`DestinationPatientProvider` / `DestinationMatchProvider`), never by these return values — the
governance separation (PLATFORM_PLAN §9).

`create_patient` hands the resolved card id to `create_appointment` through a per-run holder. That
is a tool-to-tool handoff inside one run, not a shortcut around governance: the id is only used to
*address* the write, while both steps are still verified against state the destination reports.
"""

from __future__ import annotations

from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.connectors.base import (
    DestinationCalendar,
    SourceCalendar,
    patient_from_appointment,
)
from app.providers.doctolib import DoctolibError
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaAbsence, TheveaError

_SOURCE_ERRORS = (SourceError, DoctolibError)


def import_tools(
    source: SourceCalendar,
    destination: DestinationCalendar,
    ref: str,
    *,
    source_name: str = "",
    force: bool = False,
) -> dict[str, Callable[[Any, Any], StepOutcome]]:
    """Build the import tool registry bound to this run's connectors, the appointment ref being
    copied, and whether this attempt is the forced last rung of the placement ladder (D6)."""
    # The card id resolved by `create_patient`, read by `create_appointment` in the same run.
    resolved: dict[str, int] = {}

    def _appointment() -> tuple[Any | None, StepOutcome | None]:
        try:
            appt = source.get_appointment(ref)
        except _SOURCE_ERRORS as exc:
            return None, StepOutcome(ok=False, note=f"source read failed: {exc}")
        if appt is None:
            return None, StepOutcome(ok=False, note="source appointment no longer exists")
        return appt, None

    def _create_patient(provider: Any, step: Any) -> StepOutcome:
        appt, failure = _appointment()
        if failure is not None:
            return failure
        patient = patient_from_appointment(appt, source_name)
        try:
            # STRICT: bind only on a confident match. An unknown date of birth never binds, so a
            # new card is created rather than risking someone else's card (ADR-0005 D3/D4).
            existing = destination.find_patient(patient)
            if existing is not None:
                resolved["patient_id"] = existing.id
                return StepOutcome(ok=True, note="patient card already present (claim)")
            created = destination.create_patient(patient)
        except TheveaError as exc:
            return StepOutcome(ok=False, note=f"patient card create failed: {exc}")
        resolved["patient_id"] = created.id
        return StepOutcome(ok=True, note="patient card appended (claim)")

    def _create_appointment(provider: Any, step: Any) -> StepOutcome:
        appt, failure = _appointment()
        if failure is not None:
            return failure
        patient_id = resolved.get("patient_id")
        if patient_id is None:
            # `ensure_patient` runs first, so this is the belt-and-braces path (a re-entered run).
            try:
                found = destination.find_patient(patient_from_appointment(appt, source_name))
            except TheveaError as exc:
                return StepOutcome(ok=False, note=f"patient lookup failed: {exc}")
            if found is None:
                return StepOutcome(ok=False, note="no patient card to bind the appointment to")
            patient_id = found.id
        try:
            destination.create_appointment(appt, patient_id=patient_id, force=force)
        except TheveaAbsence as exc:
            # Recorded for the trace; the orchestrator cannot read this note (the engine reports
            # the postcondition's reason), so the room ladder keys off the step status instead.
            return StepOutcome(ok=False, note=f"destination create failed (absent): {exc}")
        except TheveaError as exc:
            return StepOutcome(ok=False, note=f"destination create failed: {exc}")
        return StepOutcome(ok=True, note="appended to destination (claim)")

    return {"create_patient": _create_patient, "create_appointment": _create_appointment}
