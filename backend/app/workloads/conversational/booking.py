"""The voice agent's session: a draft the caller can correct, and governed writes.

Three kinds of tool, and the lines between them are the governance boundaries (CLAUDE.md XIII):

- DRAFT tools (`appointment_set_details`, `appointment_confirm`) write only to this session.
  Reversible, caller-visible and instant, so they are safe in real time and may be called as
  often as the caller changes their mind.
- READ tools (`search_availability`, `find_patient`, `get_patient_appointments`,
  `appointment_change_notices`) touch no system of record. But what `search_availability` returns
  is the ONLY availability that exists: an agent that offers a time this tool did not return has
  invented it. An offer is not a reservation either; the slot is re-checked as a precondition
  when the booking actually runs.
- GOVERNED tools (`appointment_book`, `cancel_appointment`, `reschedule_appointment`) write
  nothing themselves. Each runs a contract through the engine, which decides: precondition ->
  allowlist -> approval -> execute -> postcondition. A real-time surface cannot wait for a
  reviewer, so it proposes and the engine rules.

The required details are enforced twice, deliberately. The prompt asks for them — a hint the model
can drift from. The contracts' preconditions read them out of the session's own state and BLOCK
without them — the guarantee. The BLOCK reason names the missing detail, and that reason is handed
back to the model as the tool result, so the ENGINE drives the next question rather than the
prompt's memory of what it already asked.

Two things are deliberately NOT tools. The call summary email is sent by the platform when the
call ends (spec §3.9: "after every accepted call without exception") — a model that can forget to
call it is a model that can defeat the requirement. And there is no tool for "the patient agreed":
`appointment_confirm` records a confirmation over a FINGERPRINT of the details, so any later
correction silently invalidates it and the caller has to be asked again.

**Every tool result carries `agent_notes`** — one or two imperative sentences telling the agent
what to do next, computed by the platform from the state as it actually is at that moment. This is
the Wonderful house pattern and it earns its keep here for a specific reason: a rule that lives
only in the prompt competes with everything else in the prompt, and loses. Observed: the agent
collected a name and a birth date and booked without ever checking for an existing patient record,
in five runs out of five, with the rule sitting in the prompt the whole time. A note attached to
the tool result that just handed it the name is read at the one moment it is actionable.

The notes never invent policy. They restate what the state already decided — what is missing, what
was rejected and why, what the engine BLOCKed on — so they cannot drift away from the guarantees
the contracts enforce.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.config import settings
from app.connectors.base import Appointment, AppointmentLifecycle, Patient
from app.control_plane.runner import execute_contract
from app.providers.sandbox import SandboxCalendar, SandboxStateProvider, parse_slot_id
from app.templates.loader import load_template
from app.workloads.conversational.practice import load_practice

# Ask order for a new appointment. The agent asks for the first missing one, which keeps a voice
# turn to one question. Service last: a caller who does not know what they need gets the default
# (spec §4.2 step 3), so it is the detail least worth interrupting for.
FIELDS = (
    "first_name",
    "last_name",
    "date_of_birth",
    "phone",
    "preferred_time",
    "service_key",
)

_RUNBOOKS = Path(settings.templates_dir)
CONTRACT_PATH = _RUNBOOKS / "voice_appointment.yaml"
CANCEL_CONTRACT_PATH = _RUNBOOKS / "voice_appointment_cancel.yaml"
RESCHEDULE_CONTRACT_PATH = _RUNBOOKS / "voice_appointment_reschedule.yaml"

# How many alternatives to offer at once. Three is what someone can hold in their head while
# listening; a longer list on a phone call is read out and then asked for again (spec §3.1).
OFFERED_SLOTS = 3

# Every appointment this agent creates is tagged with it, so a booking of its own is always
# distinguishable from one that was already in the calendar.
REF_PREFIX = "voice-"

# The subject-line ACTION values (spec §3.9), in precedence order: the most consequential thing
# that happened during the call is what the call was.
OUTCOME_BOOKED = "NEUER TERMIN"
OUTCOME_MOVED = "TERMIN VERSCHOBEN"
OUTCOME_CANCELLED = "TERMIN ABGESAGT"
OUTCOME_CALLBACK = "RÜCKRUF ERBETEN"
OUTCOME_INFO = "NUR AUSKUNFT"
OUTCOME_INCOMPLETE = "NICHT ABGESCHLOSSEN"
OUTCOME_ERROR = "TECHNISCHER FEHLER"

# The slot key has to be an unambiguous instant, or "is this slot free?" is not a decidable
# question. The model resolves what the caller said ("half eleven tomorrow") into this shape; the
# format is validated here rather than trusted, so an unresolved phrase is refused at the draft
# instead of becoming a booking at a time nobody chose.
_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^\d{2}:\d{2}$")

_WEEKDAYS = {
    name: index
    for index, names in enumerate(
        [
            ("monday", "montag", "mo"),
            ("tuesday", "dienstag", "di"),
            ("wednesday", "mittwoch", "mi"),
            ("thursday", "donnerstag", "do"),
            ("friday", "freitag", "fr"),
            ("saturday", "samstag", "sa"),
            ("sunday", "sonntag", "so"),
        ]
    )
    for name in names
}

# How far ahead an open-ended search looks before giving up. Long enough to cross a holiday week,
# short enough that "the next free appointment" is still an answer and not a calendar dump.
SEARCH_HORIZON_DAYS = 60


# What to call each detail when telling the agent to ask for it. The field names are ours; these
# are what a receptionist would say.
_SPOKEN = {
    "first_name": "the patient's first name",
    "last_name": "the patient's last name",
    "date_of_birth": "the patient's date of birth",
    "phone": "a phone number",
    "preferred_time": "a time that suits them",
    "service_key": "which treatment they need",
}


def normalize_phone(value: str) -> str | None:
    """A spoken phone number as E.164, or None if it cannot be one (spec §3.2).

    A number with no country code is read as German: this practice is in München and its callers
    dial `0176…`, so refusing those would refuse almost everyone. `00` is the international
    prefix Germans actually say, and it means the same as `+`.

    Returning None rather than a best guess matters — the number is what a callback is made to,
    and a plausible-looking wrong number is worse than a missing one.
    """
    digits = re.sub(r"[^\d+]", "", value or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0"):
        digits = "+49" + digits[1:]
    elif digits and not digits.startswith("+"):
        digits = "+" + digits
    # 7 is shorter than any real subscriber number with a country code; 15 is E.164's own ceiling.
    return digits if re.fullmatch(r"\+\d{7,15}", digits) else None


def normalize_birthdate(value: str) -> str | None:
    """A date of birth as `YYYY-MM-DD`, or None. Refuses anything not in the plausible past —
    a misheard year is the commonest way a caller is matched to the wrong patient card."""
    if not _DAY.match(value or ""):
        return None
    try:
        born = date.fromisoformat(value)
    except ValueError:
        return None
    return value if date(1900, 1, 1) <= born < date.today() else None


class BookingSession:
    """One caller's session: the draft, the verified identity, and what the call actually did.

    Everything the summary email reports is recorded here as it happens, by the platform rather
    than by the model — the two must not be able to disagree about whether an appointment exists.
    """

    def __init__(self, session_id: str, calendar: Any | None = None) -> None:
        self._session_id = session_id
        self._practice = load_practice()
        self._calendar = calendar or SandboxCalendar(self._practice)
        self._draft: dict[str, str] = {field: "" for field in FIELDS}
        self._draft["resource"] = ""
        self._draft["prescription"] = ""
        self._draft["booking_for"] = "self"
        self._contract = load_template(CONTRACT_PATH)
        self._cancel_contract = load_template(CANCEL_CONTRACT_PATH)
        self._reschedule_contract = load_template(RESCHEDULE_CONTRACT_PATH)
        # The details as they stood when the caller last said yes. Compared, not trusted: a
        # confirmation that no longer matches the draft is not a confirmation.
        self._confirmed_fingerprint: str | None = None
        # Whether the "does this person already have a record?" check has run for the current
        # name and birth date. Reset by a correction, so a changed name is checked again.
        self._patient_checked = False
        # Fields whose read-back has already been asked for. Without this the note fires again on
        # every re-record, and the agent asks "is that number right?" twice in a row — observed
        # eating two turns of a call, which then never reached the booking at all.
        self._read_back_asked: set[str] = set()
        self._identity: dict[str, Any] = {
            "verified": False,
            "patient_id": None,
            "target_ref": None,
            "reschedule_offered": False,
            "short_notice_acknowledged": True,
        }
        # --- what the call did, for the summary the platform sends afterwards ---
        self.booked_ref: str | None = None
        self.cancelled: dict[str, Any] | None = None
        self.moved: dict[str, Any] | None = None
        self.callback: dict[str, Any] | None = None
        self.technical_error: str | None = None
        # Counted by the surface from finished caller turns, not asserted by the model: telling a
        # call that answered a question from a caller who rang off after "hello" is the difference
        # between NUR AUSKUNFT and NICHT ABGESCHLOSSEN, and the model must not get a vote.
        self.caller_turns = 0
        self.run_ids: list[str] = []

    # --- read-only views ---

    @property
    def missing(self) -> list[str]:
        return [field for field in FIELDS if not self._draft[field]]

    @property
    def calendar(self) -> Any:
        """This session's book — read-only from here; only the governed tools write.

        A `PracticeCalendar`: the in-memory sandbox in the Studio, the practice's real thevea
        calendar on a deployed instance. Nothing in this class knows which.
        """
        return self._calendar

    @property
    def can_change_appointments(self) -> bool:
        """Whether this calendar can move or cancel anything (ADR-0008).

        A capability question answered by the seam, not by configuration: `AppointmentLifecycle`
        is opted into per connector, and thevea has not given us the mutations. Asked here so the
        agent finds out BEFORE it promises a caller a cancellation, rather than after.
        """
        return isinstance(self._calendar, AppointmentLifecycle)

    @property
    def draft(self) -> dict[str, str]:
        return dict(self._draft)

    @property
    def confirmed(self) -> bool:
        return self._confirmed_fingerprint is not None and (
            self._confirmed_fingerprint == self._fingerprint()
        )

    @property
    def patient_name(self) -> str:
        name = f"{self._draft['first_name']} {self._draft['last_name']}".strip()
        return name or "unbekannt"

    # --- draft tools ---

    def set_details(self, **values: str | None) -> dict[str, Any]:
        """Record or correct any subset of the details. Returns what is still missing.

        Returning `missing` on every call is what keeps the agent on script without a script: it
        never has to remember which questions it has already asked.

        A value the practice cannot use is REJECTED rather than stored — a phone number that is
        not a phone number, a birth date in the future, a service the agent may not book on the
        phone. The rejection carries the reason, which becomes the agent's next sentence.
        """
        rejected: dict[str, str] = {}
        stored_now: list[str] = []
        for field, raw in values.items():
            if raw is None or not str(raw).strip():
                continue
            value = str(raw).strip()
            stored, why = self._validated(field, value)
            if why:
                rejected[field] = why
                continue
            if stored is not None:
                if field in ("first_name", "last_name", "date_of_birth") and (
                    self._draft[field] != stored
                ):
                    # A different person is a different lookup. Silently keeping the old verdict
                    # is how a corrected surname ends up bound to somebody else's card.
                    self._patient_checked = False
                self._draft[field] = stored
                stored_now.append(field)
        result: dict[str, Any] = {"collected": self.draft, "missing": self.missing}
        if rejected:
            result["rejected"] = rejected
        # Any change to the details invalidates an earlier yes. Reported, so the agent knows it
        # has to ask again rather than discovering it as a BLOCK at the booking.
        if not self.confirmed:
            result["confirmation_required"] = True
        result["agent_notes"] = self._notes_after_details(rejected, stored_now)
        return result

    # The two details a caller is most often misheard on, and the two the practice cannot recover
    # from being wrong: a wrong number means a callback that never arrives, a wrong birth date
    # means the wrong patient card. Spec §4.2 step 7 singles out exactly these two.
    _READ_BACK = {
        "phone": "Read the number back to the caller now and get a yes before you move on.",
        "date_of_birth": (
            "Say the date of birth back on its own and get a yes before you move on."
        ),
    }

    def _notes_after_details(
        self, rejected: dict[str, str], stored_now: list[str] | None = None
    ) -> list[str]:
        """What to do next, decided from the draft rather than from the prompt's memory."""
        notes: list[str] = []
        # Fires on the result that just accepted the value, which is the one moment the read-back
        # is a natural sentence rather than an interruption. Observed without it: "thank you, I
        # have recorded your number" and straight on to the next question.
        for field in stored_now or []:
            if field in self._READ_BACK and field not in self._read_back_asked:
                self._read_back_asked.add(field)
                notes.append(self._READ_BACK[field])
        for field, why in rejected.items():
            notes.append(
                f"{_SPOKEN.get(field, field).capitalize()} was NOT recorded: {why}. "
                "Say that to the caller and ask again — do not proceed as if you had it."
            )
        if not self._draft["preferred_time"]:
            # Ahead of the personal details on purpose: someone who spells out a surname and a
            # birth date and is then told nothing is free that week gave up their data for nothing.
            notes.append(
                "No time is agreed yet. Call search_availability and offer what it returns "
                "before asking for any personal detail."
            )
        elif missing := [f for f in self.missing if f != "preferred_time"]:
            notes.append(
                f"Still needed: {_SPOKEN.get(missing[0], missing[0])}. Ask for that one only."
            )
        elif self._identity.get("patient_id") is None and not self._patient_checked:
            # The note that closes the five-runs-out-of-five gap: it arrives attached to the very
            # result that completed the name and the date of birth.
            notes.append(
                "You now have the full name and date of birth. Call find_patient BEFORE booking, "
                "so an existing record is reused instead of duplicated."
            )
        elif not self.confirmed:
            notes.append(
                "Everything is collected. Read the name, day, time and address back, mention the "
                "privacy policy briefly, and call appointment_confirm once they say yes."
            )
        else:
            notes.append("Confirmed and complete. Call appointment_book.")
        return notes

    def _validated(self, field: str, value: str) -> tuple[str | None, str | None]:
        if field == "preferred_time":
            if not self._valid_time(value):
                return None, "not a resolvable date and time — give it as YYYY-MM-DDTHH:MM"
            if not self._calendar.in_grid(value):
                return None, (
                    "the practice has no appointment slot at that time — opening hours are "
                    "09:00 to 12:00 and 13:00 to 18:00, Monday to Friday, in 30 minute steps"
                )
            # A time the caller named directly still has to land on a real calendar. Chosen here
            # rather than at the booking so the agent learns immediately that nobody is free.
            resource = self._calendar.any_resource_free(value)
            if resource is None:
                return None, "every calendar is already booked at that time"
            self._draft["resource"] = resource
            return value, None
        if field == "slot_id":
            parsed = parse_slot_id(value)
            if parsed is None:
                return None, "not a slot that was offered"
            resource, start = parsed
            if resource not in self._calendar.schedule.resources:
                return None, "not one of the practice's calendars"
            self._draft["resource"] = resource
            self._draft["preferred_time"] = start
            return None, None
        if field == "phone":
            normalized = normalize_phone(value)
            return (normalized, None) if normalized else (None, "not a usable phone number")
        if field == "date_of_birth":
            normalized = normalize_birthdate(value)
            return (
                (normalized, None)
                if normalized
                else (None, "not a plausible date of birth — give it as YYYY-MM-DD")
            )
        if field == "service_key":
            service = self._practice.service(value)
            if service is None:
                return None, (
                    "not a treatment this practice offers — "
                    f"the usual one is {self._practice.default_service.key}"
                )
            if not service.agent_bookable:
                return None, (
                    f"{service.name} cannot be booked by phone: {service.handoff_reason}. "
                    "Take a callback request instead."
                )
            return value, None
        if field in ("first_name", "last_name", "prescription", "booking_for"):
            return value, None
        return None, "not a detail this appointment has"

    def confirm(self) -> dict[str, Any]:
        """Record that the caller has confirmed the details as they stand right now.

        The guarantee here is INVALIDATION, not veracity: nothing outside the dialogue can know
        that the caller really said yes, but the platform can and does guarantee that a yes stops
        counting the moment any detail changes. The fingerprint is over the whole draft plus the
        appointment being acted on, so correcting a birth date or picking a different slot sends
        the agent back to the read-back (spec §4.2 step 11, §6 rule 6).

        It also stamps the legal basis recorded on the patient card (spec §3.3): the data-
        processing notice and the read-back are one turn of a phone call, so they are one act.
        """
        self._confirmed_fingerprint = self._fingerprint()
        self._draft["consent_policy_id"] = self._practice.policy.consent_policy_id
        return {
            "confirmed": True,
            "details": self.draft,
            "agent_notes": [
                "Confirmation recorded. Go straight to the write — appointment_book, "
                "reschedule_appointment or cancel_appointment. If any detail changes after this, "
                "the confirmation is void and you must read back and confirm again."
            ],
        }

    # --- read tools ---

    def search_availability(
        self,
        *,
        date_from: str = "",
        date_to: str = "",
        time_window: str = "any",
        exact_time_from: str = "",
        exact_time_to: str = "",
        preferred_days: list[str] | None = None,
        limit: int = OFFERED_SLOTS,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Genuinely available slots on MA1/MA2/MA3 (spec §3.1). Read-only, answers in the turn.

        Every spoken shape the caller might use collapses into one query here: a day, a range, a
        window, a weekday preference, "after half four any day", or nothing at all. Nothing at
        all is the important case — "when is your next available appointment?" is a search from
        today to the horizon, not a question the agent should put back to the caller.

        When there is nothing to offer it says WHY. Closed, past, out of hours and full are four
        different sentences for the caller, and an agent given only an empty list picks one at
        random.
        """
        now = now or datetime.now()
        today = now.date()
        try:
            start_day = date.fromisoformat(date_from) if date_from else today
            end_day = (
                date.fromisoformat(date_to)
                if date_to
                else start_day + timedelta(days=SEARCH_HORIZON_DAYS)
            )
        except ValueError:
            return {"slots": [], "reason": "not a date — give days as YYYY-MM-DD"}
        if end_day < start_day:
            start_day, end_day = end_day, start_day
        # A search that starts in the past is not an error the caller made ("next week" resolved
        # against a stale date); it is simply clamped, so today's remaining slots still show.
        start_day = max(start_day, today)

        window, why = self._window(time_window, exact_time_from, exact_time_to)
        if why:
            return {"slots": [], "reason": why}
        weekdays = self._weekdays(preferred_days)

        slots = self._calendar.free_slots(
            date_from=start_day,
            date_to=end_day,
            window=window,
            preferred_weekdays=weekdays,
            limit=max(1, min(int(limit or OFFERED_SLOTS), OFFERED_SLOTS)),
            now=now,
        )
        if slots:
            return {
                "slots": [slot.as_dict() for slot in slots],
                "agent_notes": [
                    "These are the ONLY times that exist. Offer them in words, one at a time, "
                    "and never a time that is not in this list.",
                    "When they pick one, record it with appointment_set_details using its "
                    "slot_id.",
                ],
            }
        reason = self._nothing_free(start_day, end_day, time_window)
        return {
            "slots": [],
            "reason": reason,
            "agent_notes": [
                f"Nothing is available: {reason}. Say that, then offer to widen the search — "
                "another day, another time of day. Do not invent a time to keep the caller happy."
            ],
        }

    def find_patient(self) -> dict[str, Any]:
        """Whether this person already has a card: `none`, `unique_match` or `ambiguous`.

        Never picks between two people. Two cards sharing a name and a birth date is exactly the
        case spec §3.2 forbids the agent to resolve on the phone, and the result carries no detail
        from either record — an ambiguous answer that leaked a birth date would disclose the very
        thing the ambiguity means we cannot confirm the caller is entitled to.
        """
        if not (self._draft["first_name"] and self._draft["last_name"]):
            return {
                "result": "none",
                "reason": "no name collected yet",
                "agent_notes": ["Ask for the patient's full name first."],
            }
        if not self._draft["date_of_birth"]:
            # Matching on a name alone is how two different people become one patient card.
            return {
                "result": "none",
                "reason": "a date of birth is needed before searching",
                "agent_notes": [
                    "Ask for the date of birth before looking anyone up — a name alone is not "
                    "enough to tell two patients apart."
                ],
            }
        matches = self._calendar.match_patients(
            self._draft["first_name"],
            self._draft["last_name"],
            self._draft["date_of_birth"],
            telefon=self._draft["phone"] or None,
        )
        # Set only where the answer is usable. An `ambiguous` result is the check RAISING its
        # hand, not passing — letting it satisfy the precondition would turn the one case spec
        # §3.2 forbids the agent to resolve into the one case that unlocks the booking.
        self._patient_checked = len(matches) <= 1
        if not matches:
            return {
                "result": "none",
                "agent_notes": [
                    "No existing record. A new one will be created when the booking runs — say "
                    "nothing about records to the caller, just carry on."
                ],
            }
        if len(matches) > 1:
            return {
                "result": "ambiguous",
                "reason": "more than one record matches — hand this to the practice",
                "agent_notes": [
                    "STOP. Do not book and do not guess which record is theirs. Say the practice "
                    "will call them back, then call create_callback_request. Reveal nothing about "
                    "either record."
                ],
            }
        self._identity["patient_id"] = matches[0].id
        return {
            "result": "unique_match",
            "patient_id": matches[0].id,
            "agent_notes": [
                "This patient already has a record and it will be reused. Do not ask for their "
                "details again; carry on to the confirmation."
            ],
        }

    def get_patient_appointments(
        self,
        *,
        first_name: str,
        last_name: str,
        date_of_birth: str,
        appointment_date: str,
        appointment_time: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify the caller against a specific appointment, then return only that appointment.

        Identity is name + date of birth + the date and time of the appointment itself (spec
        §3.4). The number the caller is ringing from is not part of it: a patient may call from a
        work phone or a relative's, and refusing them would be wrong — while a matching number is
        not on its own proof of anything.

        On failure this discloses NOTHING: not the date, not the treatment, not whether any
        appointment exists at all. A caller who cannot verify learns only that the practice will
        call back, which is the same answer whether or not they are a patient (spec §3.4).
        """
        now = now or datetime.now()
        birth = normalize_birthdate(date_of_birth)
        _DENIED = [
            "Verification failed. Tell the caller NOTHING: not a date, not a treatment, not "
            "whether they have an appointment at all. Say the practice will ring them back and "
            "call create_callback_request.",
        ]
        if not (first_name and last_name and birth):
            return {
                "verified": False,
                "reason": "name and date of birth are needed",
                "agent_notes": [
                    "Ask for the full name and the date of birth. Do not confirm anything about "
                    "any appointment until this returns verified true."
                ],
            }
        if not (_DAY.match(appointment_date or "") and _CLOCK.match(appointment_time or "")):
            return {
                "verified": False,
                "reason": "the date and time of the existing appointment are needed",
                "agent_notes": [
                    "Ask them for the date AND the exact time of the appointment itself — that "
                    "is part of the identity check. Do not read any appointment out to them, and "
                    "do not work the time out for them."
                ],
            }
        matches = self._calendar.match_patients(first_name, last_name, birth)
        if len(matches) != 1:
            # Both "nobody" and "more than one" end here, and end the same way: a caller told
            # "there are two of you" has learned something about a record they may not own.
            return {
                "verified": False,
                "reason": "could not be verified — offer a callback",
                "agent_notes": _DENIED,
            }
        wanted = f"{appointment_date}T{appointment_time}"
        appointments = self._calendar.appointments_for(matches[0].id, now=now)
        target = next((a for a in appointments if a.start == wanted), None)
        if target is None:
            return {
                "verified": False,
                "reason": "could not be verified — offer a callback",
                "agent_notes": _DENIED,
            }

        self._identity.update(
            {"verified": True, "patient_id": matches[0].id, "target_ref": target.ref}
        )
        # Now that the caller has proved who they are, their name belongs in the draft — the
        # summary email names the patient "if obtained" (spec §3.9), and a move or a cancellation
        # the practice cannot attribute to anyone is a line in their inbox they have to chase.
        self._draft["first_name"] = matches[0].vorname
        self._draft["last_name"] = matches[0].nachname
        # Only the appointment the caller already named. The rest of their record stays private
        # even after a successful verification — they proved they know about this one.
        others = max(0, len(appointments) - 1)
        notes = [
            "Verified. Call appointment_change_notices next and say everything it gives you, "
            "word for word, before you move or cancel anything."
        ]
        if others:
            notes.append(
                "They have other appointments. Do NOT read them out or mention how many — if "
                "they mean a different one, ask them to name its date themselves."
            )
        return {
            "verified": True,
            "appointment": {
                "ref": target.ref,
                "start": target.start,
                "service": target.type,
                "resource": target.raw.get("resource"),
            },
            "other_upcoming": others,
            "short_notice": self._practice.is_short_notice(target.start, now=now),
            "agent_notes": notes,
        }

    def change_notices(self, action: str, *, now: datetime | None = None) -> dict[str, Any]:
        """The sentences that must be said before a cancellation or a move, and a record that
        they were issued.

        The platform hands the agent the approved wording rather than trusting it to remember it:
        these are statements about money and insurance, and an improvised paraphrase becomes a
        promise the practice has to honour (spec §5). Issuing them flips the preconditions that
        gate the write, so an agent that skipped this step is BLOCKED by the engine rather than
        corrected by a reviewer afterwards.

        What this proves is that the required wording was retrieved and recorded before the
        write, not that it was spoken — the transcript is the evidence for that. It is the
        strongest guarantee available from outside the dialogue, and it is worth more than a
        boolean the model sets about its own behaviour.
        """
        if action not in ("cancel", "reschedule"):
            return {
                "error": "action must be 'cancel' or 'reschedule'",
                "agent_notes": ["Call this again with action set to 'cancel' or 'reschedule'."],
            }
        target = self._identity.get("target_ref")
        appointment = self._calendar.find_appointment(target) if target else None
        if appointment is None:
            return {
                "error": "verify the caller against their appointment first",
                "agent_notes": [
                    "Verify them with get_patient_appointments before asking for the notices."
                ],
            }

        short_notice = self._practice.is_short_notice(appointment.start, now=now)
        say: list[str] = []
        if action == "cancel":
            say.append(self._practice.phrases["offer_reschedule"])
            self._identity["reschedule_offered"] = True
        if short_notice:
            say.append(self._practice.phrases["ausfallhonorar"])
        self._identity["short_notice_acknowledged"] = True
        notes = [
            "Say every sentence in 'say' to the caller now, as written, before you do anything "
            "else. They are the practice's own approved wording about money and options."
        ]
        if short_notice:
            notes.append(
                "This is inside the 24 hour notice period, so the Ausfallhonorar sentence "
                "applies. Say it exactly — never that a fee is automatic or required by law."
            )
        if action == "cancel":
            notes.append(
                "Offer the move ONCE. If they decline, accept it, ask for a final yes, then call "
                "cancel_appointment."
            )
        else:
            notes.append(
                "Find a free time with search_availability, record it, read the old and new "
                "appointment back, call appointment_confirm, then reschedule_appointment."
            )
        return {"say": say, "short_notice": short_notice, "agent_notes": notes}

    def create_callback_request(
        self,
        *,
        reason: str,
        urgency: str = "normal",
        callback_time: str = "",
        language: str = "de",
    ) -> dict[str, Any]:
        """Record a staff callback (spec §3.8). The one action that is always available.

        A phone number is required and nothing else is: the whole point of a callback is the case
        where the agent could not safely establish anything more. It promises no callback time
        unless the caller named one — the practice has not defined one, and inventing it is a
        commitment the agent cannot keep (spec §4.6).
        """
        phone = self._draft["phone"]
        if not phone:
            return {
                "status": "needs_phone",
                "reason": "a confirmed phone number is required",
                "agent_notes": [
                    "Ask for a phone number, read it back digit by digit, record it with "
                    "appointment_set_details, then call this again."
                ],
            }
        self.callback = {
            "name": self.patient_name,
            "phone": phone,
            "callback_time": callback_time or None,
            "language": language,
            # Stored as the caller put it. The agent is explicitly not to interpret a symptom
            # (spec §3.8, §6 rule 8) — a summary that reads "possible infection" is a diagnosis
            # nobody made.
            "reason": reason,
            "urgency": "urgent_review" if urgency == "urgent_review" else "normal",
            "call_id": self._session_id,
        }
        return {
            "status": "recorded",
            "phone": phone,
            "urgency": self.callback["urgency"],
            "agent_notes": [
                "Recorded. Tell them the practice will call back, and do NOT promise a time "
                "unless they named one themselves. Then close the call warmly."
            ],
        }

    # --- governed tools ---

    def book(self) -> dict[str, Any]:
        """Run the booking contract. The engine, not this method, decides whether it booked."""
        ref = self._ref()
        result = self._run(
            self._contract,
            SandboxStateProvider(self._calendar, self._draft_for_checks(), ref),
            {"book_appointment": self._book_tool(ref)},
        )
        if result["status"] == "ok":
            self.booked_ref = ref
            return {
                **result,
                "missing": [],
                "appointment": self._booked_summary(ref),
                "agent_notes": [
                    "The calendar confirmed it. NOW you may tell the caller it is booked — "
                    "repeat the day, the time and the address."
                ],
            }
        return {
            **result,
            "missing": self.missing,
            "appointment": None,
            "agent_notes": self._notes_after_refusal(result),
        }

    def _notes_after_refusal(self, result: dict[str, Any]) -> list[str]:
        """Turn the engine's ruling into the agent's next sentence.

        The BLOCK reason is the useful text — "the patient's date of birth is still missing" — so
        it is handed straight back rather than paraphrased into a status the agent has to
        interpret. The one thing every branch repeats is the rule the caller is harmed by: nothing
        happened, so do not say it did.
        """
        reason = result.get("reason") or "the practice system refused the action"
        if result["status"] == "error":
            return [
                "This did NOT go through. Say plainly that the action failed, do not guess "
                "whether it half-worked, and call create_callback_request."
            ]
        notes = [f"Refused: {reason}. Do NOT tell the caller anything happened."]
        if "taken" in reason:
            notes.append("Call search_availability again and offer what it returns.")
        elif "confirmed" in reason or "processed" in reason:
            notes.append("Read the details back, then call appointment_confirm.")
        elif self.missing:
            notes.append(f"Ask for {_SPOKEN.get(self.missing[0], self.missing[0])}.")
        return notes

    def cancel(self, reason: str = "", *, now: datetime | None = None) -> dict[str, Any]:
        """Run the cancellation contract. Sets the status to `abgesagt`; deletes nothing."""
        if not self.can_change_appointments:
            return self._no_lifecycle("cancelled")
        target = self._identity.get("target_ref")
        if not target:
            return {
                "status": "blocked",
                "reason": "verify the caller against their appointment first",
                "agent_notes": [
                    "Nothing was cancelled. Verify them with get_patient_appointments first."
                ],
            }
        appointment = self._calendar.find_appointment(target)
        received_at = (now or datetime.now()).strftime("%Y-%m-%dT%H:%M")
        result = self._run(
            self._cancel_contract,
            self._lifecycle_state(target),
            {"cancel_appointment": self._cancel_tool(target, reason, received_at)},
        )
        if result["status"] == "ok" and appointment is not None:
            self.cancelled = {
                "start": appointment.start,
                "received_at": received_at,
                "reason": reason or None,
                "less_than_24_hours": self._practice.is_short_notice(appointment.start, now=now),
            }
            return {
                **result,
                "agent_notes": [
                    "The calendar confirmed the cancellation. Tell the caller it is cancelled, "
                    "and that the appointment is kept on record as cancelled rather than erased."
                ],
            }
        return {**result, "agent_notes": self._notes_after_refusal(result)}

    def reschedule(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Run the move contract. One appointment moves; a second one is never created."""
        if not self.can_change_appointments:
            return self._no_lifecycle("moved")
        target = self._identity.get("target_ref")
        if not target:
            return {
                "status": "blocked",
                "reason": "verify the caller against their appointment first",
                "agent_notes": [
                    "Nothing was moved. Verify them with get_patient_appointments first."
                ],
            }
        appointment = self._calendar.find_appointment(target)
        old_start = appointment.start if appointment else ""
        new_start = self._draft["preferred_time"]
        result = self._run(
            self._reschedule_contract,
            self._lifecycle_state(target),
            {"reschedule_appointment": self._reschedule_tool(target)},
        )
        if result["status"] == "ok":
            self.moved = {
                "from": old_start,
                "to": new_start,
                "less_than_24_hours": self._practice.is_short_notice(old_start, now=now),
            }
            return {
                **result,
                "agent_notes": [
                    "The calendar confirmed the move. Tell the caller BOTH times — the one they "
                    "had and the one they now have."
                ],
            }
        return {**result, "agent_notes": self._notes_after_refusal(result)}

    def _no_lifecycle(self, verb: str) -> dict[str, Any]:
        """This calendar cannot make that change, so say so and hand over.

        Not an error and not a refusal of the caller — a capability this practice's calendar does
        not expose (ADR-0008). The honest answer is the one a receptionist would give: it needs a
        person, and someone will ring back.
        """
        return {
            "status": "unavailable",
            "reason": "this practice's calendar cannot be changed by phone",
            "agent_notes": [
                f"Nothing was {verb} and nothing can be. Tell the caller their request has been "
                "noted and the practice will call them back to sort it out, then call "
                "create_callback_request. Do not imply the change has happened or will happen "
                "automatically."
            ],
        }

    # --- the summary the platform sends when the call ends ---

    def outcome(self) -> str:
        """The single ACTION the summary's subject line carries (spec §3.9).

        Precedence, most consequential first: the practice needs to know what CHANGED before it
        needs to know what was discussed. A technical error outranks everything because it is the
        one outcome that needs a human today.
        """
        if self.technical_error:
            return OUTCOME_ERROR
        if self.booked_ref:
            return OUTCOME_BOOKED
        if self.moved:
            return OUTCOME_MOVED
        if self.cancelled:
            return OUTCOME_CANCELLED
        if self.callback:
            return OUTCOME_CALLBACK
        if any(self._draft[field] for field in FIELDS):
            # Something was being arranged and it did not finish — a dropped call, a caller who
            # rang off. Distinct from a question that was simply answered.
            return OUTCOME_INCOMPLETE
        # A real exchange that collected nothing and changed nothing was a question. One turn or
        # none was a call that did not get started, which the practice may want to ring back.
        return OUTCOME_INFO if self.caller_turns >= 2 else OUTCOME_INCOMPLETE

    def summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Everything the summary email may carry — and deliberately nothing more.

        No transcript, no audio, no date of birth, no description of a condition (spec §3.9).
        `call_id` is enough to find the call inside the protected system, and that is the point:
        the email travels over ordinary mail, so what it may safely say is less than what we know.
        """
        booked = self._calendar.find_appointment(self.booked_ref) if self.booked_ref else None
        return {
            "call_id": self._session_id,
            "at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
            "outcome": self.outcome(),
            "patient_name": self.patient_name,
            "appointment": (
                {"start": booked.start, "service": booked.type, "resource": booked.raw.get("resource")}
                if booked
                else None
            ),
            "moved": self.moved,
            "cancelled": self.cancelled,
            "callback": self.callback,
            "technical_error": self.technical_error,
            "run_ids": list(self.run_ids),
            # A short-notice change is on this list because the approved wording promises the
            # practice will look at it: "Die Praxis prüft das im Einzelfall". A flag nobody raises
            # is a case nobody checks.
            "staff_action_required": bool(
                self.callback
                or self.technical_error
                or self.outcome() == OUTCOME_INCOMPLETE
                or (self.cancelled or {}).get("less_than_24_hours")
                or (self.moved or {}).get("less_than_24_hours")
            ),
        }

    # --- internals ---

    def _run(
        self, contract: Any, provider: SandboxStateProvider, tools: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute one contract and reduce it to what the agent may act on.

        An exception here is a TECHNICAL ERROR, not a refusal, and the difference matters to the
        caller: "we could not do that" and "the system is down" lead to different next sentences,
        and only one of them needs a human today (spec §6 rule 12).
        """
        try:
            result = execute_contract(
                contract,
                case={},
                runs_dir=settings.runs_dir,
                real_providers={"sandbox": provider},
                extra_tools=tools,
            )
        except Exception as error:  # noqa: BLE001 — surfaced to the caller, never swallowed
            self.technical_error = f"{type(error).__name__}: {error}"
            return {"status": "error", "reason": "the practice system could not be reached"}
        self.run_ids.append(result.run_id)
        # The step's own reason is the useful sentence — "the patient's date of birth is still
        # missing" — so pass it through verbatim rather than paraphrasing it into a status.
        reason = next((step.reason for step in result.steps if step.reason), None)
        return {"status": result.status, "reason": reason, "run_id": result.run_id}

    def _draft_for_checks(self) -> dict[str, Any]:
        return {
            **self._draft,
            "confirmed": self.confirmed,
            "patient_checked": self._patient_checked,
        }

    def _lifecycle_state(self, target: str) -> SandboxStateProvider:
        return SandboxStateProvider(
            self._calendar,
            self._draft_for_checks(),
            self._ref(),
            identity={**self._identity, "confirmed": self.confirmed},
            target_ref=target,
        )

    def _fingerprint(self) -> str:
        """What a confirmation is a confirmation OF: every detail plus the appointment in play."""
        material = "|".join(
            [
                *(self._draft[field] for field in FIELDS),
                self._draft.get("resource", ""),
                str(self._identity.get("target_ref") or ""),
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _window(
        self, name: str, exact_from: str, exact_to: str
    ) -> tuple[tuple[time, time] | None, str | None]:
        if name == "exact_range":
            if not (_CLOCK.match(exact_from or "") and _CLOCK.match(exact_to or "")):
                return None, "an exact range needs both times as HH:MM"
            return (time.fromisoformat(exact_from), time.fromisoformat(exact_to)), None
        if name in ("", "any"):
            return None, None
        window = self._practice.window(name)
        if window is None:
            return None, (
                "unknown time window — use morning, midday, afternoon, evening, "
                "exact_range or any"
            )
        return (window.start, window.end), None

    @staticmethod
    def _weekdays(preferred: list[str] | None) -> frozenset[int] | None:
        if not preferred:
            return None
        found = {_WEEKDAYS[day.strip().casefold()] for day in preferred if day.strip().casefold() in _WEEKDAYS}
        # An unrecognised list is treated as no preference rather than as "no days match", which
        # would silently return nothing free for a caller who said something ordinary.
        return frozenset(found) or None

    def _nothing_free(self, start_day: date, end_day: date, window: str) -> str:
        if not any(
            self._calendar.is_open(start_day + timedelta(days=offset))
            for offset in range((end_day - start_day).days + 1)
        ):
            return "the practice is closed on those days"
        if window not in ("", "any"):
            return "nothing is free in that time window — offer to widen it"
        return "nothing is free in that range — offer a later date"

    @staticmethod
    def _valid_time(value: str) -> bool:
        if not _MINUTE.match(value):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True

    def _ref(self) -> str:
        """A stable id for THIS draft in THIS call — the idempotency key of spec §3.5.

        Deterministic on purpose: booking the same details twice (a dropped connection, a caller
        asking "did that go through?") reuses the ref, the append-only destination keeps the first
        write, and the postcondition confirms it — one appointment, not two. The call id alone
        would be wrong in the other direction: a caller who corrects a detail after a failed
        attempt would then re-use the ref of a booking they no longer want, so the details are in
        the material and a corrected draft is a different booking.
        """
        material = "|".join([self._session_id, *(self._draft[field] for field in FIELDS)])
        return f"{REF_PREFIX}{hashlib.sha256(material.encode()).hexdigest()[:12]}"

    def _booked_summary(self, ref: str) -> dict[str, Any]:
        appointment = self._calendar.find_appointment(ref)
        service = self._practice.service(self._draft["service_key"])
        return {
            "start": appointment.start if appointment else self._draft["preferred_time"],
            "service": service.name if service else self._draft["service_key"],
            "patient": self.patient_name,
            "address": self._practice.address,
        }

    def _book_tool(self, ref: str) -> Callable[[Any, Any], StepOutcome]:
        """The registry tool for the enforced step: find-or-create the card, append the booking.

        It reads the draft rather than taking arguments — an argument the tool can fetch is an
        argument the model can get wrong, and keeping the patient's name and birth date out of the
        tool signature keeps them out of the conversation context too. Everything it returns is a
        CLAIM; the step's postconditions re-query the calendar to decide what actually happened.
        """

        def _book(provider: Any, step: Any) -> StepOutcome:
            service = self._practice.service(self._draft["service_key"])
            patient = Patient(
                vorname=self._draft["first_name"],
                nachname=self._draft["last_name"],
                geburtsdatum=self._draft["date_of_birth"] or None,
                telefon=self._draft["phone"] or None,
                source_ref=ref,
                source="voice_agent",
            )
            card = self._calendar.find_patient(patient) or self._calendar.create_patient(patient)
            self._calendar.create_appointment(
                Appointment(
                    ref=ref,
                    start=self._draft["preferred_time"],
                    type=service.name if service else self._draft["service_key"],
                    patient=self.patient_name,
                    raw={
                        "resource": self._draft["resource"],
                        # None until the practice maps our keys to thevea's catalogue. Carried
                        # rather than invented, so an unmapped service is visible in the trace.
                        "service_id": service.service_id if service else None,
                        "source": "voice_agent",
                        "call_id": self._session_id,
                        "consent_policy_id": self._draft.get("consent_policy_id", ""),
                        "prescription": self._draft["prescription"],
                        "booking_for": self._draft["booking_for"],
                    },
                ),
                patient_id=card.id,
            )
            return StepOutcome(ok=True, note="appended to the practice calendar (claim)")

        return _book

    def _cancel_tool(
        self, ref: str, reason: str, received_at: str
    ) -> Callable[[Any, Any], StepOutcome]:
        def _cancel(provider: Any, step: Any) -> StepOutcome:
            self._calendar.cancel_appointment(ref, reason=reason or None, received_at=received_at)
            return StepOutcome(ok=True, note="status set to abgesagt (claim)")

        return _cancel

    def _reschedule_tool(self, ref: str) -> Callable[[Any, Any], StepOutcome]:
        def _move(provider: Any, step: Any) -> StepOutcome:
            moved = self._calendar.reschedule_appointment(
                ref,
                new_start=self._draft["preferred_time"],
                new_resource=self._draft["resource"],
            )
            # Even a truthful `ok=False` is only a claim; the postconditions decide. Reporting it
            # honestly still matters — it is what distinguishes "the slot went" from "the write
            # vanished" in the trace.
            return StepOutcome(ok=moved, note="appointment moved (claim)" if moved else "new slot taken")

        return _move


@dataclass(frozen=True)
class ToolSpec:
    """One tool the agent can call, defined once and consumed by every runtime that offers it.

    The live surface wraps these into Pipecat function schemas; the eval runner wraps the same
    objects into OpenAI tool definitions. That shared definition is what makes an eval result mean
    something: a scenario exercises the tools the caller actually reaches, described in the words
    the model actually reads, not a copy that drifted.
    """

    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    call: Callable[[BookingSession, dict[str, Any]], dict[str, Any]]


_SERVICE_KEYS = ", ".join(s.key for s in load_practice().bookable_services)


# Order matters. The model reads this list top-down, and with "record what the caller said" first
# it tried to answer "have you got anything on Tuesday?" by recording it — passing an invented
# time and a field that does not exist. The refusals held, but the caller heard a confused agent.
# Availability first, because availability is the first thing a caller asks about.
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_availability",
        description=(
            "Genuinely available appointment slots, and the ONLY source of availability there is. "
            "Call it for any question about what is free — 'when is your next appointment', "
            "'anything on Tuesday', 'do you have mornings', 'does 12:15 work' — before you say "
            "anything about times, and again whenever a booking is refused because the time is "
            "taken. It answers open questions too: with no arguments it returns the soonest "
            "slots, so never ask the caller to name a day just to be able to search. Returns at "
            "most three 'slots'; an empty list carries a 'reason'. Offer only the times it "
            "returns."
        ),
        properties={
            "date_from": {"type": "string", "description": "First day to look at, YYYY-MM-DD. Omit for today."},
            "date_to": {"type": "string", "description": "Last day to look at, YYYY-MM-DD. Omit to search ahead."},
            "time_window": {
                "type": "string",
                "description": "morning, midday, afternoon, evening, exact_range or any.",
            },
            "exact_time_from": {"type": "string", "description": "With time_window=exact_range: earliest HH:MM."},
            "exact_time_to": {"type": "string", "description": "With time_window=exact_range: latest HH:MM."},
            "preferred_days": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Weekday names the caller asked for, e.g. ['tuesday'].",
            },
        },
        required=(),
        call=lambda session, args: session.search_availability(
            date_from=str(args.get("date_from", "")),
            date_to=str(args.get("date_to", "")),
            time_window=str(args.get("time_window", "any")),
            exact_time_from=str(args.get("exact_time_from", "")),
            exact_time_to=str(args.get("exact_time_to", "")),
            preferred_days=args.get("preferred_days"),
        ),
    ),
    ToolSpec(
        name="appointment_set_details",
        description=(
            "Record or correct what the caller has said, for a NEW appointment being arranged. "
            "Call it as soon as you learn a detail and again whenever they change one. Returns "
            "the details still missing, and any value that was rejected with the reason. "
            "Recording a change cancels an earlier confirmation. Do NOT use it to look up an "
            "appointment the caller already has — that is get_patient_appointments."
        ),
        properties={
            "first_name": {"type": "string", "description": "The PATIENT's first name — not the caller's, when they differ."},
            "last_name": {"type": "string", "description": "The PATIENT's last name."},
            "date_of_birth": {
                "type": "string",
                "description": "The patient's date of birth as YYYY-MM-DD. Confirm it separately before recording it.",
            },
            "phone": {
                "type": "string",
                "description": "A phone number for the patient, as spoken. For a child, a parent's or guardian's number.",
            },
            "service_key": {
                "type": "string",
                "description": f"The treatment. One of: {_SERVICE_KEYS}.",
            },
            "preferred_time": {
                "type": "string",
                "description": (
                    "The appointment start as YYYY-MM-DDTHH:MM in Europe/Berlin, resolved from "
                    "what the caller said. Omit it if you cannot resolve a day and minute."
                ),
            },
            "slot_id": {
                "type": "string",
                "description": "The slot_id of an offered slot the caller chose. Preferred over preferred_time.",
            },
            "prescription": {
                "type": "string",
                "description": "muster13, privat or selbstzahler — how the treatment is being paid for.",
            },
            "booking_for": {
                "type": "string",
                "description": "self, or other when the caller is booking for someone else.",
            },
        },
        required=(),
        call=lambda session, args: session.set_details(
            **{k: v for k, v in args.items() if v not in (None, "")}
        ),
    ),
    ToolSpec(
        name="find_patient",
        description=(
            "Whether the patient already has a record here, using the name and date of birth "
            "collected so far. Returns 'none', 'unique_match' or 'ambiguous'. On 'ambiguous' "
            "stop and take a callback request — never guess which record is theirs."
        ),
        properties={},
        required=(),
        call=lambda session, args: session.find_patient(),
    ),
    ToolSpec(
        name="appointment_confirm",
        description=(
            "Record that you have read the patient's name, the date, the time and the address "
            "back and the caller said yes, and that they have been told how their data is "
            "handled. Call it immediately before booking. Any later change to the details "
            "cancels it and you must read back and confirm again."
        ),
        properties={},
        required=(),
        call=lambda session, args: session.confirm(),
    ),
    ToolSpec(
        name="appointment_book",
        description=(
            "Book the appointment from the details collected so far. Returns status 'ok' only "
            "when the calendar confirms it; any other status carries the reason. Safe to call "
            "again with the same details."
        ),
        properties={},
        required=(),
        call=lambda session, args: session.book(),
    ),
    ToolSpec(
        name="get_patient_appointments",
        description=(
            "THE ONLY way to reach an appointment the caller already has. Verify them against it "
            "and return it; nothing can be moved or cancelled until this returns verified=true. "
            "They must give the name, the date of birth AND the date and time of the "
            "appointment — those three and nothing else. Never ask for a phone number to verify "
            "someone: it is not part of the check and the caller may be on a different phone. If "
            "it returns verified=false, tell them nothing about any appointment and offer a "
            "callback."
        ),
        properties={
            "first_name": {"type": "string", "description": "The patient's first name."},
            "last_name": {"type": "string", "description": "The patient's last name."},
            "date_of_birth": {"type": "string", "description": "The patient's date of birth, YYYY-MM-DD."},
            "appointment_date": {"type": "string", "description": "The day of the existing appointment, YYYY-MM-DD."},
            "appointment_time": {"type": "string", "description": "The start of the existing appointment, HH:MM."},
        },
        required=("first_name", "last_name", "date_of_birth", "appointment_date", "appointment_time"),
        call=lambda session, args: session.get_patient_appointments(
            first_name=str(args.get("first_name", "")),
            last_name=str(args.get("last_name", "")),
            date_of_birth=str(args.get("date_of_birth", "")),
            appointment_date=str(args.get("appointment_date", "")),
            appointment_time=str(args.get("appointment_time", "")),
        ),
    ),
    ToolSpec(
        name="appointment_change_notices",
        description=(
            "The sentences you must say to the caller before cancelling or moving their "
            "appointment. Call it after verifying them and before cancel_appointment or "
            "reschedule_appointment; say everything in 'say', word for word. Without it those "
            "actions are refused."
        ),
        properties={
            "action": {"type": "string", "description": "'cancel' or 'reschedule'."},
        },
        required=("action",),
        call=lambda session, args: session.change_notices(str(args.get("action", ""))),
    ),
    ToolSpec(
        name="cancel_appointment",
        description=(
            "Cancel the verified appointment. The appointment is kept and marked cancelled, not "
            "deleted. Returns status 'ok' only when the calendar confirms it."
        ),
        properties={
            "reason": {
                "type": "string",
                "description": "Only if the caller volunteered one, in their own words. Never your interpretation.",
            }
        },
        required=(),
        call=lambda session, args: session.cancel(str(args.get("reason", ""))),
    ),
    ToolSpec(
        name="reschedule_appointment",
        description=(
            "Move the verified appointment to the new time already recorded with "
            "appointment_set_details. One appointment is moved; no second one is created. "
            "Returns status 'ok' only when the calendar confirms the new time."
        ),
        properties={},
        required=(),
        call=lambda session, args: session.reschedule(),
    ),
    ToolSpec(
        name="create_callback_request",
        description=(
            "Ask the practice to call the caller back. Use it whenever you cannot help safely: "
            "an ambiguous patient record, a failed verification, a medical or billing question, "
            "a technical failure, a service you may not book, or ANY request to speak to a "
            "person — a caller who asks for a human gets a callback, never an explanation of why "
            "they cannot have one. Needs a phone number, so ask for one if you do not have it."
        ),
        properties={
            "reason": {
                "type": "string",
                "description": "What the caller asked for, in their words. Do not interpret a symptom.",
            },
            "urgency": {"type": "string", "description": "'normal' or 'urgent_review'."},
            "callback_time": {"type": "string", "description": "A time the caller said suits them, if any."},
        },
        required=("reason",),
        call=lambda session, args: session.create_callback_request(
            reason=str(args.get("reason", "")),
            urgency=str(args.get("urgency", "normal")),
            callback_time=str(args.get("callback_time", "")),
        ),
    ),
)
