"""The mirror write tool (ADR-0006 D2) — publish one day's occupancy to the booking website.

One tool, one governed step. It reads the practice calendar for the day and replaces the
website's copy of that day. Replacing is the point: appending could never free a time again after
an appointment is cancelled or moved. It writes only rooms and times (plus the website's own
booking ref where the appointment came from there) — no patient, no note, no procedure.

Like every tool here it only CLAIMS success; whether the website really shows it is decided by
the step's postcondition re-reading both systems (`MirrorDayProvider`), never by this return value.
"""

from __future__ import annotations

from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.connectors.base import AvailabilityMirror, OccupancySource
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaError


def mirror_tools(
    occupancy: OccupancySource,
    site: AvailabilityMirror,
    day: str,
    rooms: list[int],
) -> dict[str, Callable[[Any, Any], StepOutcome]]:
    """Build the mirror tool registry bound to this run's two connectors and the day being
    published (the day travels in the case — one governed run per day)."""

    def _publish_busy_day(provider: Any, step: Any) -> StepOutcome:
        try:
            ranges = occupancy.list_busy(day, rooms)
        except TheveaError as exc:
            return StepOutcome(ok=False, note=f"practice calendar read failed: {exc}")
        try:
            site.publish_day(day, ranges)
        except SourceError as exc:
            return StepOutcome(ok=False, note=f"website publish failed: {exc}")
        return StepOutcome(ok=True, note=f"published {len(ranges)} busy ranges for {day} (claim)")

    return {"publish_busy_day": _publish_busy_day}
