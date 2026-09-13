"""The Studio's sandbox calendar — a real practice calendar whose book is in memory.

The voice agent books through exactly the seam a deployed practice uses (ADR-0004). Only the
transport differs: this one appends to a per-session dict instead of thevea's GraphQL API, so
pointing the Studio at a real calendar is a connection change rather than a code change.

It implements two protocols, and the split is the governance story:

- `DestinationCalendar` — read + CREATE only. No `update`, no `delete`, because the protocol has
  none (ADR-0004 D7 / ADR-0005 D1). A repeated create for a ref already in the book is ignored
  rather than overwritten, which is both append-only and what makes a retry after a dropped
  connection idempotent instead of a duplicate.
- `AppointmentLifecycle` — exactly two named transitions, cancel and reschedule (ADR-0008).
  Neither destroys anything: cancelling sets a STATUS and keeps the record, rescheduling moves
  the start, and both append to the appointment's own history. There is still no way to make an
  appointment stop existing, and still no generic mutation.

The grid comes from the practice knowledge base (`practice.py`), not from constants here: the
12:00–13:00 break is *outside every opening period*, so no code path can produce a slot inside it,
and the three bookable calendars MA1/MA2/MA3 are named in configuration rather than in code.

`SandboxStateProvider` is the read side the engine checks against, and it reads the same objects
the tools write through — never a tool's return value:

- `request`  -> is each required detail actually collected? (booking preconditions)
- `identity` -> has the caller been verified, and did they confirm? (lifecycle preconditions)
- `calendar` -> is the slot free, did the write land, is the appointment now cancelled/moved?
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from laufwise.state.base import StateView

from app.connectors.base import Appointment, Patient, PatientRef
from app.workloads.conversational.practice import Practice, Schedule, load_practice

# Statuses an appointment can hold. thevea's own word for cancelled is `abgesagt` (spec §3.7); it
# is written here rather than translated so the sandbox and a real connector agree on the value
# a postcondition checks for.
BOOKED = "gebucht"
CANCELLED = "abgesagt"

_MINUTE_FMT = "%Y-%m-%dT%H:%M"


def _minute(value: datetime) -> str:
    return value.strftime(_MINUTE_FMT)


@dataclass(frozen=True)
class Slot:
    """One offerable appointment start on one calendar.

    `slot_id` is the offer's identity and it is DERIVED, not allocated: the same start on the
    same calendar always produces the same id, so re-checking an offer needs no reservation table
    and a caller repeating "the half past nine one" lands on the same slot. `expires_at` is how
    long the agent may keep quoting it (spec §3.1); the slot is re-checked before booking
    regardless, because an offer is not a reservation.
    """

    slot_id: str
    start: str
    end: str
    resource: str
    expires_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "slot_id": self.slot_id,
            "start": self.start,
            "end": self.end,
            "resource": self.resource,
            "expires_at": self.expires_at,
        }


# `MA1@2026-09-07T09:30` — the resource, then the minute it starts.
_SLOT_ID = re.compile(r"^(?P<resource>[A-Za-z0-9_-]+)@(?P<start>\d{4}-\d{2}-\d{2}T\d{2}:\d{2})$")


def parse_slot_id(slot_id: str) -> tuple[str, str] | None:
    """`(resource, start)` for a well-formed slot id, else None.

    Validated rather than trusted: the model hands this back, and an id it composed itself must
    not become a booking on a calendar that does not exist.
    """
    match = _SLOT_ID.match(slot_id or "")
    return (match.group("resource"), match.group("start")) if match else None


# How long an offered slot stays quotable. Long enough to finish collecting a name and a date of
# birth, short enough that a caller is not still holding it after the conversation moved on.
OFFER_TTL = timedelta(minutes=10)


class SandboxCalendar:
    """In-memory practice calendar. One per voice session."""

    def __init__(self, practice: Practice | None = None) -> None:
        self._practice = practice or load_practice()
        self._appointments: dict[str, Appointment] = {}
        self._patients: list[PatientRef] = []
        # Kept beside the card rather than on it: `PatientRef` is the destination's handle shape
        # (ADR-0005) and inventing fields on it would be a change to the seam, not to the sandbox.
        self._contact: dict[int, str] = {}

    @property
    def schedule(self) -> Schedule:
        return self._practice.schedule

    # --- DestinationCalendar (read) ---

    def find_appointment(self, ref: str) -> Appointment | None:
        return self._appointments.get(ref)

    def find_patient(self, patient: Patient, *, strict: bool = True) -> PatientRef | None:
        """The single card for this person, or None.

        Strict binding needs a date of birth, exactly as a real destination does (ADR-0005 D3) —
        and the voice agent now collects one (spec §3.2), so the identity a booking binds to is
        the same identity the practice would type by hand.

        None when the answer is AMBIGUOUS, too: two people who share a name and a birth date are
        not a patient this can pick between, and picking one would be the exact failure spec §3.2
        forbids. `match_patients` is what the agent uses to see that difference.
        """
        found = self.match_patients(
            patient.vorname, patient.nachname, patient.geburtsdatum, strict=strict
        )
        return found[0] if len(found) == 1 else None

    def match_patients(
        self,
        vorname: str,
        nachname: str,
        geburtsdatum: str | None,
        *,
        strict: bool = True,
        telefon: str | None = None,
    ) -> list[PatientRef]:
        """Every card that matches, so the caller can tell `unique_match` from `ambiguous`.

        Name comparison folds case, spaces and hyphens (spec §3.2), which is what makes
        "Anna-Maria Weber" and "anna maria weber" the same person. It does NOT fold umlauts into
        digraphs: `Müller` and `Mueller` are one person to a receptionist, so both are tried.

        The phone number narrows an otherwise ambiguous result but never widens one — a matching
        number cannot make a different name into the same patient (spec §3.2: "phone number is
        used as an additional check").
        """
        matches = [
            card
            for card in self._patients
            if _same_name(card.vorname, vorname)
            and _same_name(card.nachname, nachname)
            and (not strict or (geburtsdatum and card.geburtsdatum == geburtsdatum))
        ]
        if len(matches) > 1 and telefon:
            narrowed = [c for c in matches if self._contact.get(c.id) == telefon]
            if narrowed:
                return narrowed
        return matches

    # --- DestinationCalendar (create only — no update, no delete) ---

    def create_patient(self, patient: Patient) -> PatientRef:
        card = PatientRef(
            id=len(self._patients) + 1,
            vorname=patient.vorname,
            nachname=patient.nachname,
            geburtsdatum=patient.geburtsdatum,
        )
        self._patients.append(card)
        if patient.telefon:
            self._contact[card.id] = patient.telefon
        return card

    def create_appointment(
        self, appt: Appointment, *, patient_id: int, force: bool = False
    ) -> None:
        # First write wins. Append-only means an existing ref is never replaced, which also makes
        # a repeated attempt (a dropped connection, a caller asking "did that go through?")
        # idempotent instead of a second appointment.
        self._appointments.setdefault(
            appt.ref,
            replace(
                appt,
                raw={
                    **appt.raw,
                    "patient_id": patient_id,
                    "status": BOOKED,
                    "history": [{"at": _minute(datetime.now()), "event": "created", "start": appt.start}],
                },
            ),
        )

    def close(self) -> None:
        return None

    # --- AppointmentLifecycle (ADR-0008: two named transitions, neither destroys) ---

    def cancel_appointment(
        self, ref: str, *, reason: str | None = None, received_at: str
    ) -> None:
        """Move the appointment to `abgesagt`. The record and its history stay (spec §4.5 #7)."""
        appt = self._appointments.get(ref)
        if appt is None:
            return
        entry: dict[str, Any] = {"at": received_at, "event": "cancelled"}
        if reason:
            entry["reason"] = reason
        self._appointments[ref] = replace(
            appt,
            raw={
                **appt.raw,
                "status": CANCELLED,
                "cancelled_at": received_at,
                "history": [*appt.raw.get("history", []), entry],
            },
        )

    def reschedule_appointment(self, ref: str, *, new_start: str, new_resource: str) -> bool:
        """Move an appointment. False — and nothing changed — if the new slot is already taken.

        The order matters and is the whole of spec §3.6's "atomically": the new slot is tested
        BEFORE the old appointment is touched, so a caller cannot end up with neither. And it is
        a move, not a create — no second appointment is left behind (§3.6, last bullet).
        """
        appt = self._appointments.get(ref)
        if appt is None or appt.raw.get("status") != BOOKED:
            return False
        if not self.is_free(new_start, new_resource, ignoring=ref):
            return False
        self._appointments[ref] = replace(
            appt,
            start=new_start,
            end=self._end_of(new_start),
            raw={
                **appt.raw,
                "resource": new_resource,
                "history": [
                    *appt.raw.get("history", []),
                    {
                        "at": _minute(datetime.now()),
                        "event": "rescheduled",
                        "from": appt.start,
                        "to": new_start,
                    },
                ],
            },
        )
        return True

    # --- sandbox-only reads, used by the tools and the state provider ---

    @property
    def appointments(self) -> list[Appointment]:
        return list(self._appointments.values())

    def status_of(self, ref: str) -> str | None:
        appt = self._appointments.get(ref)
        return None if appt is None else str(appt.raw.get("status", ""))

    def history_of(self, ref: str) -> list[dict[str, Any]]:
        appt = self._appointments.get(ref)
        return list(appt.raw.get("history", [])) if appt else []

    def is_free(self, start: str, resource: str, *, ignoring: str | None = None) -> bool:
        """Whether that calendar is free at that minute. A cancelled appointment frees its slot."""
        return not any(
            appt.start == start
            and appt.raw.get("resource") == resource
            and appt.raw.get("status") == BOOKED
            and appt.ref != ignoring
            for appt in self._appointments.values()
        )

    def any_resource_free(self, start: str) -> str | None:
        """The first of MA1/MA2/MA3 that is free at `start`, or None. All three are equivalent
        (spec §3.1), so the caller never has to pick one and the agent never has to explain one."""
        return next(
            (r for r in self.schedule.resources if self.is_free(start, r)), None
        )

    def free_slots(
        self,
        *,
        date_from: date,
        date_to: date,
        window: tuple[time, time] | None = None,
        preferred_weekdays: frozenset[int] | None = None,
        limit: int = 3,
        now: datetime | None = None,
    ) -> list[Slot]:
        """The bookable starts in a date range, soonest first — the grid minus what is taken.

        One slot per START, not per calendar: three equivalent calendars would otherwise offer
        the same time three times, and a caller read "half past nine, half past nine, half past
        nine" has been given one option and told it was three.

        A slot in the past is never offered: the caller cannot take it, and an agent that reads
        one out has told them something false.
        """
        now = now or datetime.now()
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
                    resource = self.any_resource_free(_minute(start))
                    if resource is None:
                        continue
                    found.append(self._slot(start, resource, now))
            day += timedelta(days=1)
        return found

    def appointments_for(
        self, patient_id: int, *, upcoming_only: bool = True, now: datetime | None = None
    ) -> list[Appointment]:
        """This patient's appointments, soonest first. Only open, future ones by default —
        those are the only ones a caller can reschedule or cancel (spec §3.4)."""
        now = now or datetime.now()
        found = [
            appt
            for appt in self._appointments.values()
            if appt.raw.get("patient_id") == patient_id
            and (
                not upcoming_only
                or (appt.raw.get("status") == BOOKED and _parsed(appt.start) > now)
            )
        ]
        return sorted(found, key=lambda a: a.start)

    def has_card(self, vorname: str, nachname: str) -> bool:
        return any(
            _same_name(card.vorname, vorname) and _same_name(card.nachname, nachname)
            for card in self._patients
        )

    def is_open(self, day: date) -> bool:
        return self.schedule.is_open(day)

    def in_grid(self, start: str) -> bool:
        """Whether `start` is a real slot start — open day, inside a period, on the grid.

        The one check that makes 12:15 unbookable no matter who asks for it. A time the caller
        names directly never goes through `free_slots`, so without this the break exists only in
        what is *offered*, not in what can be *booked*.
        """
        moment = _parsed(start)
        return moment is not None and moment in self.schedule.starts_on(moment.date())

    def _slot(self, start: datetime, resource: str, now: datetime) -> Slot:
        return Slot(
            slot_id=f"{resource}@{_minute(start)}",
            start=_minute(start),
            end=self._end_of(_minute(start)),
            resource=resource,
            expires_at=_minute(now + OFFER_TTL),
        )

    def _end_of(self, start: str) -> str:
        moment = _parsed(start)
        if moment is None:
            return ""
        return _minute(moment + timedelta(minutes=self.schedule.slot_minutes))


# Case, spaces and hyphens are noise in a spoken name; the umlaut digraph is a real spelling
# variant a receptionist would treat as the same person (spec §3.2).
_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def _fold(value: str) -> str:
    folded = (value or "").casefold()
    for umlaut, digraph in _UMLAUTS.items():
        folded = folded.replace(umlaut, digraph)
    return re.sub(r"[\s\-']", "", folded)


def _same_name(a: str, b: str) -> bool:
    return bool(a) and bool(b) and _fold(a) == _fold(b)


def _parsed(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _MINUTE_FMT)
    except (ValueError, TypeError):
        return None


class SandboxStateProvider:
    """Serves the `request`, `identity` and `calendar` bindings of the voice contracts.

    Constructed per governed run from the live draft and this session's calendar, so every check
    reads what is actually there at that moment — the postcondition re-resolves it after the tool
    has run, which is what catches a write that claimed success and persisted nothing.
    """

    def __init__(
        self,
        calendar: SandboxCalendar,
        draft: dict[str, Any],
        ref: str,
        *,
        identity: dict[str, Any] | None = None,
        target_ref: str | None = None,
    ) -> None:
        self._calendar = calendar
        self._draft = draft
        self._ref = ref
        self._identity = identity or {}
        # The appointment a cancel/reschedule acts on — a different appointment from the one a
        # booking creates, which is why it is a separate field rather than a reuse of `ref`.
        self._target = target_ref

    def query(self, name: str, params: dict | None = None) -> StateView:
        if name == "request":
            return StateView(value=self._request())
        if name == "identity":
            return StateView(value=self._identity_view())
        return StateView(value=self._calendar_view())

    def _request(self) -> dict[str, Any]:
        return {
            "has_first_name": bool(self._draft.get("first_name")),
            "has_last_name": bool(self._draft.get("last_name")),
            "has_date_of_birth": bool(self._draft.get("date_of_birth")),
            "has_phone": bool(self._draft.get("phone")),
            "has_preferred_time": bool(self._draft.get("preferred_time")),
            "has_service": bool(self._draft.get("service_key")),
            "has_consent": bool(self._draft.get("consent_policy_id")),
            # Whether the duplicate check has actually run for THIS name and birth date (spec
            # §3.2: "Do not create a duplicate until the search has been completed"). An
            # ambiguous result deliberately does not satisfy it.
            "patient_checked": bool(self._draft.get("patient_checked")),
            # Explicit spoken confirmation of name, date, time and place (spec §3.5 #1). A
            # boolean the SURFACE sets from a caller's "yes" — the model cannot set it, because
            # the tool that flips it takes no arguments from the model's own reasoning.
            "confirmed": bool(self._draft.get("confirmed")),
        }

    def _identity_view(self) -> dict[str, Any]:
        return {
            "verified": bool(self._identity.get("verified")),
            "confirmed": bool(self._identity.get("confirmed")),
            # Whether the Ausfallhonorar sentence was actually said when it applied (spec §3.7).
            # False only when it applied and was skipped, so a normal-notice cancellation is not
            # blocked by a warning nobody needed to hear.
            "short_notice_acknowledged": bool(
                self._identity.get("short_notice_acknowledged", True)
            ),
            # Offering a move once before cancelling is a spec requirement (§4.5 #2), so it is a
            # precondition rather than a line in the prompt the model can drift past.
            "reschedule_offered": bool(self._identity.get("reschedule_offered")),
        }

    def _calendar_view(self) -> dict[str, Any]:
        booked = self._calendar.find_appointment(self._ref)
        start = str(self._draft.get("preferred_time", ""))
        resource = str(self._draft.get("resource", ""))
        target = self._calendar.find_appointment(self._target) if self._target else None
        return {
            # Free if a calendar is open at that minute, or if what occupies it is this very
            # draft, or if it is the appointment being moved already sitting there — otherwise a
            # retry of a booking or a move that already landed would block itself.
            "slot_free": (
                bool(self._calendar.any_resource_free(start))
                or booked is not None
                or (target is not None and target.start == start)
            ),
            # A time the caller named directly still has to be a real slot start (spec §3.1: the
            # 12:00–13:00 break is never bookable, by any route).
            "slot_on_grid": self._calendar.in_grid(start),
            "resource_allowed": resource in self._calendar.schedule.resources,
            "booking_confirmed": booked is not None,
            "patient_card_confirmed": self._calendar.has_card(
                str(self._draft.get("first_name", "")), str(self._draft.get("last_name", ""))
            ),
            # --- the lifecycle bindings, read off the TARGET appointment ---
            "appointment_exists": target is not None,
            "appointment_open": target is not None and target.raw.get("status") == BOOKED,
            "appointment_cancelled": target is not None
            and target.raw.get("status") == CANCELLED,
            # The record survived the cancellation and can still be read back with its history
            # — this is what "preserve, do not delete" means as a check rather than a promise.
            "appointment_preserved": target is not None
            and bool(self._calendar.history_of(self._target or "")),
            "appointment_moved": target is not None
            and target.start == str(self._draft.get("preferred_time", "")),
            "appointment_still_open": target is not None
            and target.raw.get("status") == BOOKED,
        }
