"""The mirror's state binding (ADR-0006 D1) — does the website show what the practice calendar
has for this day?

One binding, `site_day`, answers one question: `in_sync`. It reads BOTH systems live — the
practice calendar (thevea) and the website's copy — and compares them, the same shape as
`DestinationPatientProvider`, which also grounds on two live reads. The comparison itself is a
pure function of what came back (`busy_digest`), so the check stays a predicate over state and
never trusts a write's claim.

It drives both halves of the contract:
- precondition  `site_day.in_sync == false` — a day already in sync is a governed skip, not a write.
- postcondition `site_day.in_sync == true`  — the publish is only "done" if the website's own read
  agrees with a fresh read of the practice calendar.

Either system failing raises `StateUnavailable`, so the run halts and the website keeps whatever
it had — never a false "in sync" and never a half-published day.
"""

from __future__ import annotations

from laufwise.state.base import StateUnavailable, StateView

from app.connectors.base import AvailabilityMirror, OccupancySource, busy_digest
from app.providers.healthyfeet import SourceError
from app.providers.thevea import TheveaError


class MirrorDayProvider:
    def __init__(
        self,
        occupancy: OccupancySource,
        site: AvailabilityMirror,
        day: str,
        rooms: list[int],
    ) -> None:
        self._occupancy = occupancy
        self._site = site
        self._day = day
        self._rooms = rooms

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            live = self._occupancy.list_busy(self._day, self._rooms)
        except TheveaError as exc:
            raise StateUnavailable(f"practice calendar unavailable: {exc}") from exc
        try:
            published = self._site.read_day(self._day)
        except SourceError as exc:
            raise StateUnavailable(f"website unavailable: {exc}") from exc
        # `published is None` = the website never mirrored this day, which is NOT "in sync with an
        # empty day": a day with no appointments still has to be published once, or the website
        # goes on offering it from its own bookings alone.
        in_sync = published is not None and busy_digest(published) == busy_digest(live)
        return StateView(
            value={
                "in_sync": in_sync,
                "day": self._day,
                "occupied": len(live),
                "published": -1 if published is None else len(published),
            }
        )
