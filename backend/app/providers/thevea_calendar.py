"""The real practice calendar, over thevea — what the voice agent books into in production.

`TheveaConnector` was built for the import path: find one appointment by its ref, find or create a
card, append. A caller on the phone asks harder questions — what is free on Tuesday morning, do I
already have a record, which appointments could I move — so this adapts that connector to the
wider `PracticeCalendar` port without widening the connector itself.

Two things it does NOT do, both deliberate:

- **It does not implement `AppointmentLifecycle`.** thevea exposes no mutation we have been given
  for the `abgesagt` status transition or for moving an appointment, and the practice
  specification (§7) asks for an official API or written authorization before we go looking. So
  the capability is *absent*, and absent is a working state: `isinstance(cal, AppointmentLifecycle)`
  is False, the change-appointment tools refuse with a reason the agent can say out loud, and the
  caller gets a callback instead of a lie. That is exactly what an opt-in protocol is for
  (ADR-0008). When the practice supplies the mutations, this class grows two methods and nothing
  else in the platform changes.
- **It does not invent the room mapping.** MA1/MA2/MA3 are thevea `mandantMitarbeiterId`s that
  nobody has told us, so they are configuration. Without them this refuses to construct rather
  than guessing an id and booking into a stranger's calendar.

Availability is DERIVED, not fetched: thevea has no "free slots" query, so the practice's own grid
(`practice.yaml` — opening periods, 30-minute steps, the 12:00–13:00 break) minus what `getTermine`
reports as booked IS the availability. That subtraction happening here rather than in the agent is
what makes the 12:00 break unbookable on the real calendar for the same reason it is unbookable on
the sandbox: the slot is never generated.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.thevea import TheveaError, _to_utc

from app.connectors.base import Appointment, Patient, PatientRef
from app.providers.sandbox import Slot, _same_name
from app.workloads.conversational.practice import Practice, load_practice

_MINUTE_FMT = "%Y-%m-%dT%H:%M"

# How long an offered slot stays quotable, matching the sandbox. An offer is not a reservation on
# either calendar; the slot is re-checked as a precondition when the booking runs.
OFFER_TTL = timedelta(minutes=10)


class TheveaCalendarUnconfigured(RuntimeError):
    """Raised when the practice's rooms have not been mapped to thevea ids.

    Loud on purpose. The alternative — quietly falling back to the in-memory sandbox — would give
    a caller a real-sounding appointment in a calendar nobody reads (ADR-0003 D4, anti-fabrication).
    """


class TheveaPracticeCalendar:
    """`PracticeCalendar` over thevea. Create-only; no lifecycle (see the module docstring).

    `rooms` maps the practice's own calendar names to thevea room ids — `{"MA1": 4711, ...}` — and
    must cover every resource the practice knowledge base names, or construction fails.
    """

    def __init__(
        self,
        connector: Any,
        rooms: dict[str, int],
        practice: Practice | None = None,
    ) -> None:
        self._practice = practice or load_practice()
        missing = [r for r in self._practice.schedule.resources if r not in rooms]
        if missing:
            raise TheveaCalendarUnconfigured(
                f"thevea room ids are not configured for {', '.join(missing)} — set them on the "
                "calendar connection before this agent can book into the real calendar"
            )
        self._connector = connector
        self._rooms = {name: int(rooms[name]) for name in self._practice.schedule.resources}
        self._by_id = {room_id: name for name, room_id in self._rooms.items()}

    @property
    def schedule(self):
        return self._practice.schedule

    def close(self) -> None:
        self._connector.close()

    # --- reads -------------------------------------------------------------------------------

    def _booked_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Every appointment thevea holds in the window, across all three rooms.

        One query for the whole range rather than one per day: a caller asking "when is your next
        appointment?" spans weeks, and a query per day would put a phone call on hold while it ran.
        """
        return self._connector.termine_between(
            start.replace(tzinfo=ZoneInfo(self.schedule.timezone)),
            end.replace(tzinfo=ZoneInfo(self.schedule.timezone)),
            room_ids=list(self._rooms.values())
        )

    def _occupied(self, start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
        """Busy intervals, including off-grid starts and appointments longer than one slot.

        Provider data must be readable before we can assert availability. An invalid interval
        raises instead of silently freeing a room.
        """
        taken: list[tuple[str, datetime, datetime]] = []
        for termin in self._booked_between(start, end):
            if str(termin.get("status") or "").lower() in ("abgesagt", "cancelled"):
                continue
            try:
                room = self._by_id.get(int(termin.get("mandantMitarbeiterId") or -1))
                if room is None:
                    raise ValueError("unknown room")
                zone = ZoneInfo(self.schedule.timezone)
                begins = _to_utc(termin["from"]).astimezone(zone).replace(tzinfo=None)
                finishes = _to_utc(termin["until"]).astimezone(zone).replace(tzinfo=None)
                if finishes <= begins:
                    raise ValueError("invalid interval")
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise TheveaError("cannot verify availability: unreadable appointment") from error
            taken.append((room, begins, finishes))
        return taken

    def _overlaps(self, taken, resource: str, start: datetime) -> bool:
        end = start + timedelta(minutes=self.schedule.slot_minutes)
        return any(room == resource and begins < end and finishes > start
                   for room, begins, finishes in taken)

    def free_slots(
        self,
        *,
        date_from: date,
        date_to: date,
        window: tuple[Any, Any] | None = None,
        preferred_weekdays: frozenset[int] | None = None,
        limit: int = 3,
        now: datetime | None = None,
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
                for start in self.schedule.starts_on(day):
                    if len(found) >= limit:
                        break
                    if start <= now:
                        continue
                    if window and not (window[0] <= start.time() < window[1]):
                        continue
                    minute = start.strftime(_MINUTE_FMT)
                    room = next(
                        (r for r in self._rooms if not self._overlaps(taken, r, start)), None
                    )
                    if room is None:
                        continue
                    found.append(
                        Slot(
                            slot_id=f"{room}@{minute}",
                            start=minute,
                            end=(start + timedelta(minutes=self.schedule.slot_minutes)).strftime(
                                _MINUTE_FMT
                            ),
                            resource=room,
                            expires_at=(now + OFFER_TTL).strftime(_MINUTE_FMT),
                        )
                    )
            day += timedelta(days=1)
        return found

    def any_resource_free(self, start: str) -> str | None:
        when = _parsed(start)
        if when is None:
            return None
        taken = self._occupied(when, when + timedelta(minutes=self.schedule.slot_minutes))
        return next((r for r in self._rooms if not self._overlaps(taken, r, when)), None)

    def is_free(self, start: str, resource: str, *, ignoring: str | None = None) -> bool:
        when = _parsed(start)
        if when is None or resource not in self._rooms:
            return False
        taken = self._occupied(when, when + timedelta(minutes=self.schedule.slot_minutes))
        return not self._overlaps(taken, resource, when)

    def in_grid(self, start: str) -> bool:
        when = _parsed(start)
        return when is not None and when in self.schedule.starts_on(when.date())

    def is_open(self, day: date) -> bool:
        return self.schedule.is_open(day)

    def match_patients(
        self,
        vorname: str,
        nachname: str,
        geburtsdatum: str | None,
        *,
        strict: bool = True,
        telefon: str | None = None,
    ) -> list[PatientRef]:
        """Every card matching name + date of birth. Ambiguity is REPORTED, never resolved here.

        Goes through the connector's own candidate search, so thevea's spelling quirks — the
        surname-initial paging, the typo tolerance — apply to a phone caller exactly as they do to
        an import.
        """
        if not (vorname and nachname):
            return []
        found: list[PatientRef] = []
        for node in self._connector.match_candidates(nachname):
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            if not (
                _same_name(str(node.get("vorname") or ""), vorname)
                and _same_name(str(node.get("nachname") or ""), nachname)
            ):
                continue
            born = str(node.get("geburtsdatum") or "")[:10]
            if strict and (not geburtsdatum or born != geburtsdatum):
                continue
            found.append(
                PatientRef(
                    id=int(node["id"]),
                    vorname=str(node.get("vorname") or ""),
                    nachname=str(node.get("nachname") or ""),
                    geburtsdatum=born or None,
                )
            )
        return found

    def appointments_for(
        self, patient_id: int, *, upcoming_only: bool = True, now: datetime | None = None
    ) -> list[Appointment]:
        now = now or datetime.now(ZoneInfo(self.schedule.timezone)).replace(tzinfo=None)
        horizon = now + timedelta(days=365)
        found: list[Appointment] = []
        for termin in self._booked_between(now if upcoming_only else now - timedelta(days=365), horizon):
            if int(termin.get("patientId") or -1) != int(patient_id):
                continue
            if upcoming_only and str(termin.get("status") or "").lower() in (
                "abgesagt",
                "cancelled",
            ):
                continue
            when = _local_minute(termin.get("from"))
            if when is None:
                continue
            found.append(
                Appointment(
                    ref=str(termin.get("id") or ""),
                    start=when,
                    type=termin.get("bemerkung"),
                    raw={
                        **termin,
                        "resource": self._by_id.get(
                            int(termin.get("mandantMitarbeiterId") or -1), ""
                        ),
                        "status": termin.get("status") or "gebucht",
                    },
                )
            )
        return sorted(found, key=lambda a: a.start)

    def find_appointment(self, ref: str) -> Appointment | None:
        return self._connector.find_appointment(ref)

    def find_patient(self, patient: Patient, *, strict: bool = True) -> PatientRef | None:
        found = self.match_patients(
            patient.vorname, patient.nachname, patient.geburtsdatum, strict=strict
        )
        # Never picks between two people — the same rule the sandbox follows and the same rule
        # spec §3.2 states. Ambiguity is the caller's cue to hand over, not ours to break.
        return found[0] if len(found) == 1 else None

    def has_card(self, vorname: str, nachname: str) -> bool:
        return bool(self.match_patients(vorname, nachname, None, strict=False))

    # --- writes (create only) ----------------------------------------------------------------

    def create_patient(self, patient: Patient) -> PatientRef:
        return self._connector.create_patient(patient)

    def create_appointment(
        self, appt: Appointment, *, patient_id: int, force: bool = False
    ) -> None:
        """Append into the room the draft chose. Never `force` — a phone caller is not an
        emergency, and bypassing thevea's own validation on a live call is how an appointment
        lands somewhere the practice is absent (ADR-0005 D6)."""
        resource = str((appt.raw or {}).get("resource") or "")
        room_id = self._rooms.get(resource)
        if room_id is None:
            raise TheveaCalendarUnconfigured(f"no thevea room mapped for {resource!r}")
        self._connector.create_appointment_in_room(appt, patient_id=patient_id, room_id=room_id)

    # --- sandbox-shaped reads the state provider uses ----------------------------------------

    def status_of(self, ref: str) -> str | None:
        found = self.find_appointment(ref)
        return None if found is None else str(found.raw.get("status") or "gebucht")

    def history_of(self, ref: str) -> list[dict[str, Any]]:
        """thevea keeps no history we can read, so this is empty rather than invented.

        It matters: the cancellation postcondition checks that the record SURVIVED, and an empty
        history would fail it — which is correct, because this calendar cannot cancel at all.
        """
        return []


def _parsed(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _MINUTE_FMT)
    except (ValueError, TypeError):
        return None


def _local_minute(instant: Any) -> str | None:
    """A thevea Instant (`2026-09-07T07:00:00.000Z`) as a local `YYYY-MM-DDTHH:MM`.

    thevea stores UTC and the practice thinks in Europe/Berlin, so an hour lost here is an
    appointment offered at the wrong time — the conversion goes through the connector's own
    helper rather than being repeated with a different rounding.
    """
    from app.providers.thevea import _to_utc

    if not instant:
        return None
    try:
        from zoneinfo import ZoneInfo

        return (
            _to_utc(str(instant))
            .astimezone(ZoneInfo(load_practice().schedule.timezone))
            .strftime(_MINUTE_FMT)
        )
    except Exception:  # noqa: BLE001 — an unreadable instant is a slot we simply do not offer
        return None
