"""The import write tool (ADR-0004 D6/D7).

`create_appointment` reads the appointment from the SOURCE (by ref) and APPENDS it to the
DESTINATION. It is the only write in the contract, and the destination connector exposes only
create — so the tool cannot modify or delete anything (append-only, D7).

The tool CLAIMS success when the destination accepts the write; whether it actually persisted is
decided by the postcondition's independent re-query (`DestinationMatchProvider`), never this
return value — the governance separation (PLATFORM_PLAN §9).
"""

from __future__ import annotations

from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.connectors.base import DestinationCalendar, SourceCalendar
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaError


def import_tools(
    source: SourceCalendar, destination: DestinationCalendar, ref: str
) -> dict[str, Callable[[Any, Any], StepOutcome]]:
    """Build the import tool registry bound to the source + destination connectors and the
    appointment ref being copied this run."""

    def _create_appointment(provider: Any, step: Any) -> StepOutcome:
        try:
            appt = source.get_appointment(ref)
        except SourceError as exc:
            return StepOutcome(ok=False, note=f"source read failed: {exc}")
        if appt is None:
            return StepOutcome(ok=False, note="source appointment no longer exists")
        try:
            destination.create_appointment(appt)
        except TheveaError as exc:
            return StepOutcome(ok=False, note=f"destination create failed: {exc}")
        return StepOutcome(ok=True, note="appended to destination (claim)")

    return {"create_appointment": _create_appointment}