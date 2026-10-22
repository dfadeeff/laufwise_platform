"""Availability as the practice's grid minus what the system has booked (ADR-0014 D3).

No practice system we connect has a "free slots" query. Thevea and Doctolib both report what is
booked, per calendar, and the practice's own schedule says which starts exist at all, so the
availability a caller hears is that subtraction. It lives here once, because two systems doing it
two ways is two chances to offer the 12:00 break, or a time that is already taken.

A system supplies only `_occupied`: every busy interval in a range, per practice calendar label.
It must raise if an appointment cannot be read. Freeing a slot because a row was unreadable is the
one mistake this class exists to make impossible.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.sandbox import Slot

MINUTE_FMT = "%Y-%m-%dT%H:%M"

# How long an offered slot stays quotable, matching the sandbox. An offer is not a reservation on
# any calendar; the slot is re-checked as a precondition when the booking runs.
OFFER_TTL = timedelta(minutes=10)


def parsed_minute(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, MINUTE_FMT)
    except (ValueError, TypeError):
        return None


class DerivedAvailability:
    """The availability half of `PracticeCalendar`, for systems that only report what is booked.

    Subclasses set `self._practice` and `self._resources` (the practice's calendar labels, in the
    order a free one is chosen) and implement `_occupied`.
    """

    _practice: Any
    _resources: tuple[str, ...]

    def _occupied(self, start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
        """Busy `(calendar label, begins, finishes)` intervals overlapping the range, local time."""
        raise NotImplementedError

    @property
    def schedule(self):
        return self._practice.schedule

    def _length(self, minutes: int | None) -> timedelta:
        return timedelta(minutes=minutes or self.schedule.slot_minutes)

    def _overlaps(self, taken, resource: str, start: datetime, minutes: int | None = None) -> bool:
        end = start + self._length(minutes)
        return any(
            room == resource and begins < end and finishes > start
            for room, begins, finishes in taken
        )

    def free_slots(
        self,
        *,
        date_from: date,
        date_to: date,
        window: tuple[Any, Any] | None = None,
        preferred_weekdays: frozenset[int] | None = None,
        limit: int = 3,
        now: datetime | None = None,
        minutes: int | None = None,
    ) -> list[Slot]:
        now = now or datetime.now(ZoneInfo(self.schedule.timezone)).replace(tzinfo=None)
        taken = self._occupied(
            datetime.combine(date_from, datetime.min.time()),
            datetime.combine(date_to, datetime.max.time()),
        )
        found: list[Slot] = []
        day = date_from
        while day <= date_to and len(found) < limit:
            if preferred_weekdays is None or day.weekday() in preferred_weekdays:
                for start in self.schedule.starts_on(day, minutes):
                    if len(found) >= limit:
                        break
                    if start <= now:
                        continue
                    if window and not (window[0] <= start.time() < window[1]):
                        continue
                    minute = start.strftime(MINUTE_FMT)
                    room = next(
                        (r for r in self._resources if not self._overlaps(taken, r, start, minutes)),
                        None,
                    )
                    if room is None:
                        continue
                    found.append(
                        Slot(
                            slot_id=f"{room}@{minute}",
                            start=minute,
                            end=(start + self._length(minutes)).strftime(MINUTE_FMT),
                            resource=room,
                            expires_at=(now + OFFER_TTL).strftime(MINUTE_FMT),
                        )
                    )
            day += timedelta(days=1)
        return found

    def any_resource_free(self, start: str, minutes: int | None = None) -> str | None:
        when = parsed_minute(start)
        if when is None:
            return None
        taken = self._occupied(when, when + self._length(minutes))
        return next(
            (r for r in self._resources if not self._overlaps(taken, r, when, minutes)), None
        )

    def is_free(
        self, start: str, resource: str, *, ignoring: str | None = None, minutes: int | None = None
    ) -> bool:
        when = parsed_minute(start)
        if when is None or resource not in self._resources:
            return False
        taken = self._occupied(when, when + self._length(minutes))
        return not self._overlaps(taken, resource, when, minutes)

    def in_grid(self, start: str, minutes: int | None = None) -> bool:
        when = parsed_minute(start)
        return when is not None and when in self.schedule.starts_on(when.date(), minutes)

    def is_open(self, day: date) -> bool:
        return self.schedule.is_open(day)
