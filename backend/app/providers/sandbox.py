"""The Studio's sandbox calendar — a real `DestinationCalendar` whose book is in memory.

The Studio voice agent books through exactly the seam a deployed practice uses (ADR-0004). Only
the transport differs: this one appends to a per-session dict instead of thevea's GraphQL API, so
pointing the Studio at a real calendar is a connection change rather than a code change.

It inherits the seam's most important property by construction: there is no `update` and no
`delete`, because the protocol has none (ADR-0004 D7 / ADR-0005 D1). A caller who wants a
different time corrects the draft *before* it is created; nothing can rewrite it afterwards. A
repeated create for a ref already in the book is ignored rather than overwritten — which is both
append-only and what makes a retry after a dropped connection idempotent instead of a duplicate.

`SandboxStateProvider` is the read side the engine checks against, and it reads the same two
objects the tool writes through — never the tool's return value:

- `request`  -> is each required detail actually collected? (the three preconditions)
- `calendar` -> is the requested time free, and did the write land? (the pre- and postconditions)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

from laufwise.state.base import StateView

from app.connectors.base import Appointment, Patient, PatientRef


class SandboxCalendar:
    """In-memory `DestinationCalendar`. One per Studio voice session.

    The opening hours below are this destination's own rule, kept inside the connector the way
    thevea's working-hours check lives in the thevea connector (ADR-0004). Nothing above the
    connector should have to know when this practice is open.
    """

    OPENS = time(9, 0)
    CLOSES = time(17, 0)
    SLOT_MINUTES = 30
    OPEN_WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Monday–Friday

    def __init__(self) -> None:
        self._appointments: dict[str, Appointment] = {}
        self._patients: list[PatientRef] = []

    # --- DestinationCalendar (read) ---

    def find_appointment(self, ref: str) -> Appointment | None:
        return self._appointments.get(ref)

    def find_patient(self, patient: Patient, *, strict: bool = True) -> PatientRef | None:
        """Strict binding needs a date of birth, exactly as a real destination does (ADR-0005 D3).

        A voice caller gives a name, not a birth date, so strict lookup never matches and every
        booking opens its own card. That is the honest behaviour of the real seam, kept rather
        than relaxed here: a sandbox that binds on names alone would teach the agent a rule no
        production connector will honour.
        """
        if strict and not patient.geburtsdatum:
            return None
        for card in self._patients:
            same_name = (
                card.vorname.casefold() == patient.vorname.casefold()
                and card.nachname.casefold() == patient.nachname.casefold()
            )
            if same_name and (not strict or card.geburtsdatum == patient.geburtsdatum):
                return card
        return None

    # --- DestinationCalendar (create only — no update, no delete) ---

    def create_patient(self, patient: Patient) -> PatientRef:
        card = PatientRef(
            id=len(self._patients) + 1,
            vorname=patient.vorname,
            nachname=patient.nachname,
            geburtsdatum=patient.geburtsdatum,
        )
        self._patients.append(card)
        return card

    def create_appointment(
        self, appt: Appointment, *, patient_id: int, force: bool = False
    ) -> None:
        # First write wins. Append-only means an existing ref is never replaced, which also makes
        # a repeated attempt (a dropped connection, a caller asking "did that go through?")
        # idempotent instead of a second appointment.
        self._appointments.setdefault(
            appt.ref, replace(appt, raw={**appt.raw, "patient_id": patient_id})
        )

    def close(self) -> None:
        return None

    # --- sandbox-only reads, used by the state provider ---

    @property
    def appointments(self) -> list[Appointment]:
        return list(self._appointments.values())

    def is_free(self, start: str) -> bool:
        return not any(appt.start == start for appt in self._appointments.values())

    def free_slots(self, day: date, *, limit: int, now: datetime | None = None) -> list[str]:
        """The bookable starts left on `day`, soonest first — the real grid minus what is taken.

        A slot already in the past is not offered: the caller cannot take it, and an agent that
        reads one out has told them something false. `now` is injectable so this stays testable
        without freezing the clock.
        """
        if day.weekday() not in self.OPEN_WEEKDAYS:
            return []
        now = now or datetime.now()
        cursor = datetime.combine(day, self.OPENS)
        closing = datetime.combine(day, self.CLOSES)
        slots: list[str] = []
        while cursor < closing and len(slots) < limit:
            start = cursor.strftime("%Y-%m-%dT%H:%M")
            if cursor > now and self.is_free(start):
                slots.append(start)
            cursor += timedelta(minutes=self.SLOT_MINUTES)
        return slots

    def is_open(self, day: date) -> bool:
        return day.weekday() in self.OPEN_WEEKDAYS

    def has_card(self, vorname: str, nachname: str) -> bool:
        return any(
            card.vorname.casefold() == vorname.casefold()
            and card.nachname.casefold() == nachname.casefold()
            for card in self._patients
        )


class SandboxStateProvider:
    """Serves the `request` and `calendar` bindings of the `voice_appointment` contract.

    Constructed per governed run from the live draft and this session's calendar, so every check
    reads what is actually there at that moment — the postcondition re-resolves it after the tool
    has run, which is what catches a write that claimed success and persisted nothing.
    """

    def __init__(self, calendar: SandboxCalendar, draft: dict[str, str], ref: str) -> None:
        self._calendar = calendar
        self._draft = draft
        self._ref = ref

    def query(self, name: str, params: dict | None = None) -> StateView:
        if name == "request":
            return StateView(
                value={
                    "has_first_name": bool(self._draft.get("first_name")),
                    "has_last_name": bool(self._draft.get("last_name")),
                    "has_preferred_time": bool(self._draft.get("preferred_time")),
                }
            )
        booked = self._calendar.find_appointment(self._ref)
        start = self._draft.get("preferred_time", "")
        return StateView(
            value={
                # Free if nothing occupies the slot, or if what occupies it is this very draft —
                # otherwise a retry of a booking that already landed would block itself.
                "slot_free": self._calendar.is_free(start) or booked is not None,
                "booking_confirmed": booked is not None,
                "patient_card_confirmed": self._calendar.has_card(
                    self._draft.get("first_name", ""), self._draft.get("last_name", "")
                ),
            }
        )
