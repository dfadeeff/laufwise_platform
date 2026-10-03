"""A practice's Doctolib calendar, as a live call reads it (ADR-0014).

Availability only, for now. Doctolib's read API is verified (memory `doctolib-pro-recon`); its write
calls, patient search and creating an appointment, have never been captured. So this implements
the availability half of `PracticeCalendar` and nothing else, and the registry declares exactly
that: `capabilities = {"availability"}`. An agent on Doctolib tells callers which times are free
and passes their choice to staff as a callback. When the writes are captured, this class gains the
booking methods and the registry entry gains `patients` and `booking`; nothing else changes.

Availability is derived, as for Thevea: the practice's grid minus what each mapped agenda reports
booked. Every row occupies its slot unless its status says it was cancelled. A status nobody has
seen can therefore only make the agent offer less, never a time that is taken.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.derived_availability import DerivedAvailability
from app.providers.doctolib import DoctolibError
from app.workloads.conversational.practice import Practice, load_practice

# Statuses that free the slot. Seen live: done, confirmed, no_show_but_ok, rescheduled (all occupy).
_FREES_SLOT = frozenset({"canceled", "cancelled", "deleted", "canceled_by_patient", "canceled_by_doctor"})


class DoctolibCalendarUnconfigured(RuntimeError):
    """The practice's calendar labels have not all been mapped to Doctolib agendas.

    Loud for the same reason as Thevea's: guessing an agenda would read a stranger's calendar.
    """


class DoctolibPracticeCalendar(DerivedAvailability):
    """The availability half of `PracticeCalendar` over Doctolib's agenda read.

    `agendas` maps the practice's own calendar labels to Doctolib agenda ids, `{"MA1": "2570190"}`,
    and must cover every label the practice books into.
    """

    def __init__(self, connector: Any, agendas: dict[str, str], practice: Practice | None = None) -> None:
        self._practice = practice or load_practice()
        missing = [r for r in self._practice.schedule.resources if not agendas.get(r)]
        if missing:
            raise DoctolibCalendarUnconfigured(
                f"Doctolib agenda ids are not configured for {', '.join(missing)} — map them on the "
                "calendar connection before this agent can read the real calendar"
            )
        self._connector = connector
        self._agendas = {name: str(agendas[name]) for name in self._practice.schedule.resources}
        self._by_agenda = {agenda: name for name, agenda in self._agendas.items()}
        self._resources = tuple(self._agendas)

    def close(self) -> None:
        self._connector.close()

    def _occupied(self, start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
        zone = ZoneInfo(self.schedule.timezone)
        taken: list[tuple[str, datetime, datetime]] = []
        # Widened by a day: a row is matched on its own start, and an appointment that began before
        # the window can still be running inside it.
        rows = self._connector.appointments_between(
            start - timedelta(days=1), end, list(self._agendas.values())
        )
        for agenda, row in rows:
            if str(row.get("status") or "").lower() in _FREES_SLOT:
                continue
            try:
                begins = _local(row["start_date"], zone)
                finishes = _local(row["end_date"], zone)
                if finishes <= begins:
                    raise ValueError("invalid interval")
            except (KeyError, TypeError, ValueError) as error:
                raise DoctolibError("cannot verify availability: unreadable appointment") from error
            taken.append((self._by_agenda[agenda], begins, finishes))
        return taken


def _local(value: str, zone: ZoneInfo) -> datetime:
    """Doctolib's ISO time with its own offset, as a naive local time in the practice's zone."""
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(zone).replace(tzinfo=None)
