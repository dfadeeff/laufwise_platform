"""State providers that expose a connector's read side to the engine (ADR-0004).

The import contract has two state bindings, both answering "does this appointment exist here?":
- `source_appt` -> `SourceAppointmentProvider`: is the appointment real in the source? (grounds
  the copy — a stale work-list can't cause a fabricated write).
- `dest_match`  -> `DestinationMatchProvider`: has it already been imported into the destination?
  (drives the idempotency precondition AND the post-write verification).

The appointment `ref` being copied is a per-run input, supplied at construction from the case.
Any connector error becomes `StateUnavailable`, so an unreachable system BLOCKs the step
(STATE_UNAVAILABLE) rather than looking like "not there" (which could cause a duplicate write).
"""

from __future__ import annotations

from laufwise.state.base import StateUnavailable, StateView

from app.connectors.base import DestinationCalendar, SourceCalendar
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaError


class SourceAppointmentProvider:
    def __init__(self, connector: SourceCalendar, ref: str) -> None:
        self._connector = connector
        self._ref = ref

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            appt = self._connector.get_appointment(self._ref)
        except SourceError as exc:
            raise StateUnavailable(f"source calendar unavailable: {exc}") from exc
        # `.exists` is a reserved StateView accessor = "is any state present". Present -> a
        # non-empty dict (has ref); absent -> None. So `source_appt.exists` reflects reality.
        return StateView(value=({"ref": appt.ref, **appt.raw} if appt else None))


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