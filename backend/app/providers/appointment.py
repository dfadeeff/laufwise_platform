"""State providers that expose a connector's read side to the engine (ADR-0004, ADR-0005).

The import contract has three state bindings:
- `source_appt`  -> `SourceAppointmentProvider`: is the appointment real in the source? (grounds
  the copy — a stale work-list can't cause a fabricated write).
- `dest_patient` -> `DestinationPatientProvider`: does a patient card for this appointment exist
  in the destination? (verifies `ensure_patient` — the write's claim never decides).
- `dest_match`   -> `DestinationMatchProvider`: has it already been imported into the destination?
  (drives the idempotency precondition AND the post-write verification).

The appointment `ref` being copied is a per-run input, supplied at construction from the case.
Any connector error becomes `StateUnavailable`, so an unreachable system BLOCKs the step
(STATE_UNAVAILABLE) rather than looking like "not there" (which could cause a duplicate write).
"""

from __future__ import annotations

from laufwise.state.base import StateUnavailable, StateView

from app.connectors.base import (
    DestinationCalendar,
    SourceCalendar,
    patient_from_appointment,
)
from app.providers.doctolib import DoctolibError
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaError

# Any source connector's read failure must surface as StateUnavailable (BLOCK), never as an
# unhandled 500 or a false "not present" (which could cause a duplicate write). Each source
# connector raises its own error type; they are equivalent here.
_SOURCE_ERRORS = (SourceError, DoctolibError)


class SourceAppointmentProvider:
    def __init__(self, connector: SourceCalendar, ref: str) -> None:
        self._connector = connector
        self._ref = ref

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            appt = self._connector.get_appointment(self._ref)
        except _SOURCE_ERRORS as exc:
            raise StateUnavailable(f"source calendar unavailable: {exc}") from exc
        # `.exists` is a reserved StateView accessor = "is any state present". Present -> a
        # non-empty dict (has ref); absent -> None. So `source_appt.exists` reflects reality.
        return StateView(value=({"ref": appt.ref, **appt.raw} if appt else None))


class DestinationPatientProvider:
    """Answers "does a patient card for this appointment's patient exist in the destination?".

    Grounded end to end: the patient is rebuilt from a LIVE source read, not from the case, so a
    stale work-list cannot make a card look present. Uses the destination's non-strict lookup —
    the question here is "did a card land?", not "is this certainly the same human?" (the strict
    rule that decides *binding* lives in the tool, ADR-0005 D3/D4). Without that split a card
    created for a patient with no usable date of birth could never be verified.
    """

    def __init__(
        self,
        source: SourceCalendar,
        destination: DestinationCalendar,
        ref: str,
        source_name: str = "",
    ) -> None:
        self._source = source
        self._destination = destination
        self._ref = ref
        self._source_name = source_name

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            appt = self._source.get_appointment(self._ref)
        except _SOURCE_ERRORS as exc:
            raise StateUnavailable(f"source calendar unavailable: {exc}") from exc
        if appt is None:
            # No source appointment -> no patient to have a card for. `source_appt.exists` is the
            # binding that rules on that; this one simply reports absence.
            return StateView(value=None)
        try:
            found = self._destination.find_patient(
                patient_from_appointment(appt, self._source_name), strict=False
            )
        except TheveaError as exc:
            raise StateUnavailable(f"destination calendar unavailable: {exc}") from exc
        return StateView(
            value=(
                {"id": found.id, "vorname": found.vorname, "nachname": found.nachname}
                if found
                else None
            )
        )


class DestinationMatchProvider:
    def __init__(self, connector: DestinationCalendar, ref: str) -> None:
        self._connector = connector
        self._ref = ref

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            found = self._connector.find_appointment(self._ref)
        except TheveaError as exc:
            raise StateUnavailable(f"destination calendar unavailable: {exc}") from exc
        # Present -> non-empty dict (so `.exists` is true); absent -> None (`.exists` false).
        return StateView(value=({"ref": found.ref, **found.raw} if found else None))