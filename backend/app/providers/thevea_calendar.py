"""The real practice calendar, over thevea — what the voice agent books into in production.

`TheveaConnector` was built for the import path: find one appointment by its ref, find or create a
card, append. A caller on the phone asks harder questions — what is free on Tuesday morning, do I
already have a record, which appointments could I move — so this adapts that connector to the
wider `PracticeCalendar` port without widening the connector itself.

Two things it does NOT do, both deliberate:

- **It implements `AppointmentLifecycle` with exactly its two transitions** (ADR-0021, on the
  practice owner's authorization of 4 October 2026): cancel sets thevea's status to `ABGESAGT`
  and keeps the record; move rewrites the start and room. Both go through thevea's own
  `updatePatientenTermin`, which replaces the whole appointment, so they copy every field as just
  read and change only theirs. Nothing here can delete an appointment.
- **It does not invent the room mapping.** MA1/MA2/MA3 are thevea `mandantMitarbeiterId`s that
  nobody has told us, so they are configuration. Without them this refuses to construct rather
  than guessing an id and booking into a stranger's calendar.

Availability is DERIVED, not fetched: thevea has no "free slots" query, so the practice's own grid
(`practice.yaml` — opening periods, 30-minute steps, the 12:00–13:00 break) minus what thevea
reports as booked — appointments AND absences, which are two different lists over there — IS the
availability. That subtraction happening here rather than in the agent is
what makes the 12:00 break unbookable on the real calendar for the same reason it is unbookable on
the sandbox: the slot is never generated.

A room on holiday or at a training course is subtracted the same way (ADR-0011). Without that the
agent offers a time, the caller agrees to it, and only the write finds out — thevea refuses it with
`errorTypes: ['ABWESENHEIT']` — so the caller is told no after being told yes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.thevea import TheveaError, _absent_days, _to_utc

from app.connectors.base import Appointment, Patient, PatientRef
from app.providers.derived_availability import (
    MINUTE_FMT,
    DerivedAvailability,
    parsed_minute,
)
from app.providers.sandbox import _same_name
from app.workloads.conversational.practice import Practice, load_practice

# The grid-minus-booked availability is shared with every system that derives it (ADR-0014 D3).
_MINUTE_FMT = MINUTE_FMT


class TheveaCalendarUnconfigured(RuntimeError):
    """Raised when the practice's rooms have not been mapped to thevea ids.

    Loud on purpose. The alternative — quietly falling back to the in-memory sandbox — would give
    a caller a real-sounding appointment in a calendar nobody reads (ADR-0003 D4, anti-fabrication).
    """


def _with_zone(value: str, zone: ZoneInfo) -> str:
    """A naive local timestamp made explicit. Anything already carrying an offset is untouched."""
    text = (value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        return value
    return parsed.replace(tzinfo=zone).isoformat()


class TheveaPracticeCalendar(DerivedAvailability):
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
        self._resources = tuple(self._rooms)

    def close(self) -> None:
        self._connector.close()

    # --- reads -------------------------------------------------------------------------------

    def _booked_between(
        self, start: datetime, end: datetime
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Appointments AND absences thevea holds in the window, across every room.

        One query for the whole range rather than one per day: a caller asking "when is your next
        appointment?" spans weeks, and a query per day would put a phone call on hold while it ran.
        """
        return self._connector.occupancy_between(
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
        termine, absences = self._booked_between(start, end)
        for node in absences:
            absence = _absent_days(node)
            room = self._by_id.get(absence[0]) if absence else None
            if absence is None or room is None:
                # Same rule as an unreadable appointment: state we cannot read is never read as
                # "free". A holiday we failed to parse would put a caller in an empty practice.
                raise TheveaError("cannot verify availability: unreadable absence")
            _room_id, begins, ends = absence
            taken.append(
                (
                    room,
                    datetime.combine(begins, datetime.min.time()),
                    datetime.combine(ends + timedelta(days=1), datetime.min.time()),
                )
            )
        for termin in termine:
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
        # Appointments only: "which appointments does this patient have?" is not a question an
        # absence can answer.
        termine, _absences = self._booked_between(
            now if upcoming_only else now - timedelta(days=365), horizon
        )
        for termin in termine:
            if int(termin.get("patientId") or -1) != int(patient_id):
                continue
            if upcoming_only and str(termin.get("status") or "").lower() in (
                "abgesagt",
                "cancelled",
            ):
                continue
            appointment = self._as_appointment(termin)
            if appointment is not None:
                found.append(appointment)
        return sorted(found, key=lambda a: a.start)

    def find_appointment(self, ref: str) -> Appointment | None:
        """By thevea id (what `appointments_for` hands out, so a caller's existing appointment —
        including one the practice entered itself — can be found again), else by our own booking
        ref in the note (what a booking's postcondition looks for)."""
        if ref.isdigit():
            node = self._node(int(ref))
            return self._as_appointment(node) if node else None
        return self._connector.find_appointment(ref)

    def _node(self, termin_id: int) -> dict[str, Any] | None:
        """One appointment as thevea returns it, looked up by id across the booking horizon."""
        now = datetime.now(ZoneInfo(self.schedule.timezone)).replace(tzinfo=None)
        termine, _absences = self._booked_between(now - timedelta(days=1), now + timedelta(days=365))
        return next((t for t in termine if str(t.get("id")) == str(termin_id)), None)

    def _as_appointment(self, termin: dict[str, Any]) -> Appointment | None:
        when = _local_minute(termin.get("from"), self.schedule.timezone)
        if when is None:
            return None
        cancelled = str(termin.get("status") or "").upper() == "ABGESAGT"
        return Appointment(
            ref=str(termin.get("id") or ""),
            start=when,
            type=termin.get("bemerkung"),
            raw={
                **termin,
                "resource": self._by_id.get(int(termin.get("mandantMitarbeiterId") or -1), ""),
                "status": "abgesagt" if cancelled else "gebucht",
            },
        )

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
        self._connector.create_appointment_in_room(
            self._localised(appt), patient_id=patient_id, room_id=room_id
        )

    def _localised(self, appt: Appointment) -> Appointment:
        """Stamp the practice's timezone onto a naive time before it is written.

        The connector's rule — a naive timestamp is UTC — is right for the import, whose source
        systems speak UTC. It is wrong here: the voice tier's times come off the practice GRID,
        which is local, so "2026-09-21T14:00" means two o'clock in Munich. Written as UTC it
        became four o'clock — and the reads, which localise correctly, then still showed the
        caller's slot as free, so the same slot could be booked again and again.

        Observed against the live practice calendar on 21 September 2026: three appointments
        asked for at 14:00 all landed at 16:00, and each one left 14:00 bookable behind it.
        """
        zone = ZoneInfo(self.schedule.timezone)
        return replace(
            appt,
            start=_with_zone(appt.start, zone),
            end=_with_zone(appt.end, zone) if appt.end else appt.end,
        )

    # --- sandbox-shaped reads the state provider uses ----------------------------------------

    def status_of(self, ref: str) -> str | None:
        found = self.find_appointment(ref)
        return None if found is None else str(found.raw.get("status") or "gebucht")

    def history_of(self, ref: str) -> list[dict[str, Any]]:
        """What can be read back of the appointment: its current state, or nothing if it is gone.

        thevea keeps no change history we can read, so nothing past is claimed here. What the
        cancellation postcondition needs is that the record SURVIVED the cancellation, and a
        record re-read with its status is exactly that; a deleted one returns [] and fails it.
        """
        found = self.find_appointment(ref)
        return [] if found is None else [{"event": "read", "status": found.raw.get("status")}]

    # --- the two lifecycle transitions (ADR-0008, on thevea since ADR-0021) -------------------

    def cancel_appointment(self, ref: str, *, reason: str | None = None, received_at: str) -> None:
        """Set the appointment to ABGESAGT. The record stays, with a note saying when and how."""
        node = self._node(int(ref)) if ref.isdigit() else None
        if node is None:
            raise TheveaError(f"no appointment {ref} to cancel")
        remark = f"ABGESAGT per Telefon {received_at}" + (f" ({reason})" if reason else "")
        # In front, so our own booking ref stays last in the note.
        self._connector.update_patienten_termin(
            node,
            status="ABGESAGT",
            bemerkung=" · ".join(p for p in (remark, node.get("bemerkung")) if p),
        )

    def reschedule_appointment(self, ref: str, *, new_start: str, new_resource: str) -> bool:
        """Move the appointment, keeping its length. False, with nothing changed, if the new slot
        is taken: it is tested before the appointment is touched (ADR-0008 D3)."""
        node = self._node(int(ref)) if ref.isdigit() else None
        room_id = self._rooms.get(new_resource)
        if node is None or room_id is None or str(node.get("status") or "").upper() == "ABGESAGT":
            return False
        if not self.is_free(new_start, new_resource):
            return False
        zone = ZoneInfo(self.schedule.timezone)
        begins = datetime.fromisoformat(new_start).replace(tzinfo=zone)
        length = _to_utc(node["until"]) - _to_utc(node["from"])
        self._connector.update_patienten_termin(
            node, start=begins, until=begins + length, room_id=room_id
        )
        return True


_parsed = parsed_minute


def _local_minute(instant: Any, timezone: str | None = None) -> str | None:
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
            .astimezone(ZoneInfo(timezone or load_practice().schedule.timezone))
            .strftime(_MINUTE_FMT)
        )
    except Exception:  # noqa: BLE001 — an unreadable instant is a slot we simply do not offer
        return None
