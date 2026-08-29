"""The Studio booking agent's session: a draft the caller can correct, and one governed write.

Two tools, and the line between them is the governance boundary (CLAUDE.md XIII):

- `appointment_set_details` writes only to the DRAFT. Reversible, caller-visible and instant, so
  it is safe in real time and may be called as often as the caller changes their mind. This is
  what "update" means for a conversational agent here — the appointment itself is create-only.
- `appointment_find_slots` reads availability. Read-only and reversible, so by the same rule it
  needs no governed step — but what it returns is the ONLY availability that exists: an agent
  that offers a time this tool did not return has invented it. The offer is not a reservation
  either; the slot is re-checked as a precondition when the booking actually runs.
- `appointment_book` is the consequential one, and it writes nothing itself. It runs the
  `voice_appointment` contract through the engine, which decides: precondition -> allowlist ->
  approval -> execute -> postcondition. A real-time surface cannot wait for a reviewer, so it
  proposes and the engine rules.

The three required details are enforced twice, deliberately. The prompt asks for them — a hint
the model can drift from. The contract's preconditions read them out of the draft's own state and
BLOCK without them — the guarantee. The BLOCK reason names the missing detail, and that reason is
handed back to the model as the tool result, so the ENGINE drives the next question rather than
the prompt's memory of what it already asked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.config import settings
from app.connectors.base import Appointment, Patient
from app.control_plane.runner import execute_contract
from app.providers.sandbox import SandboxCalendar, SandboxStateProvider
from app.templates.loader import load_template

# Ask order. The agent asks for the first missing one, which keeps a voice turn to one question.
FIELDS = ("first_name", "last_name", "preferred_time")

CONTRACT_PATH = Path(settings.templates_dir) / "voice_appointment.yaml"

# How many alternatives to offer at once. Three is what someone can hold in their head while
# listening; a longer list on a phone call is read out and then asked for again.
OFFERED_SLOTS = 3

# Every appointment this agent creates is tagged with it, so a booking of its own is always
# distinguishable from one that was already in the calendar.
REF_PREFIX = "studio-"

# The slot key has to be an unambiguous instant, or "is this slot free?" is not a decidable
# question. The model resolves what the caller said ("half eleven tomorrow") into this shape; the
# format is validated here rather than trusted, so an unresolved phrase is refused at the draft
# instead of becoming a booking at a time nobody chose.
_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


class BookingSession:
    """One caller's booking state. Lives for the length of a Studio voice session."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._calendar = SandboxCalendar()
        self._draft: dict[str, str] = {field: "" for field in FIELDS}
        self._contract = load_template(CONTRACT_PATH)

    @property
    def missing(self) -> list[str]:
        return [field for field in FIELDS if not self._draft[field]]

    @property
    def calendar(self) -> SandboxCalendar:
        """This session's sandbox book — read-only from here; only the governed tool writes."""
        return self._calendar

    @property
    def draft(self) -> dict[str, str]:
        return dict(self._draft)

    def set_details(self, **values: str | None) -> dict[str, Any]:
        """Record or correct any subset of the three details. Returns what is still missing.

        Returning `missing` on every call is what keeps the agent on script without a script: it
        never has to remember which questions it has already asked.
        """
        rejected: dict[str, str] = {}
        for field in FIELDS:
            value = values.get(field)
            if value is None or not str(value).strip():
                continue
            value = str(value).strip()
            if field == "preferred_time" and not self._valid_time(value):
                rejected[field] = (
                    "not a resolvable date and time — give it as YYYY-MM-DDTHH:MM"
                )
                continue
            self._draft[field] = value
        return {"collected": self.draft, "missing": self.missing, **({"rejected": rejected} if rejected else {})}

    def find_slots(self, day: str) -> dict[str, Any]:
        """Bookable times on `day`. Read-only, so it answers inside the turn.

        When there is nothing to offer it says WHY — closed, past, or full are three different
        sentences for the caller, and an agent given only an empty list will pick one at random.
        """
        try:
            when = date.fromisoformat(day)
        except ValueError:
            return {"day": day, "slots": [], "reason": "not a date — give it as YYYY-MM-DD"}
        slots = self._calendar.free_slots(when, limit=OFFERED_SLOTS)
        if slots:
            return {"day": day, "slots": slots}
        if when < date.today():
            reason = "that day has already passed"
        elif not self._calendar.is_open(when):
            reason = "the practice is closed that day"
        else:
            reason = "that day is fully booked"
        return {"day": day, "slots": [], "reason": reason}

    def book(self) -> dict[str, Any]:
        """Run the governed contract. The engine, not this method, decides whether it booked."""
        ref = self._ref()
        result = execute_contract(
            self._contract,
            case={},
            runs_dir=settings.runs_dir,
            real_providers={"sandbox": SandboxStateProvider(self._calendar, self.draft, ref)},
            extra_tools={"book_appointment": self._tool(ref)},
        )
        # The step's own reason is the useful sentence — "the caller's last name is still
        # missing" — so pass it through verbatim rather than paraphrasing it into a status.
        reason = next((step.reason for step in result.steps if step.reason), None)
        return {
            "status": result.status,
            "reason": reason,
            "missing": self.missing,
            "appointment": self.draft if result.status == "ok" else None,
            "run_id": result.run_id,
        }

    # --- internals ---

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
        """A stable id for THIS draft in THIS session.

        Deterministic on purpose: booking the same details twice (a dropped connection, a caller
        asking "did that go through?") reuses the ref, the append-only destination keeps the first
        write, and the postcondition confirms it — one appointment, not two. Changing a detail
        changes the ref, so a corrected draft is a different booking.
        """
        material = "|".join([self._session_id, *(self._draft[field] for field in FIELDS)])
        return f"{REF_PREFIX}{hashlib.sha256(material.encode()).hexdigest()[:12]}"

    def _tool(self, ref: str) -> Callable[[Any, Any], StepOutcome]:
        """The registry tool for the enforced step: find-or-create the card, append the booking.

        It reads the draft rather than taking arguments — an argument the tool can fetch is an
        argument the model can get wrong, and keeping the caller's name out of the tool signature
        keeps it out of the conversation context too. Everything it returns is a CLAIM; the
        step's postconditions re-query the calendar to decide what actually happened.
        """

        def _book(provider: Any, step: Any) -> StepOutcome:
            patient = Patient(
                vorname=self._draft["first_name"],
                nachname=self._draft["last_name"],
                source_ref=ref,
                source="studio",
            )
            card = self._calendar.find_patient(patient) or self._calendar.create_patient(patient)
            self._calendar.create_appointment(
                Appointment(ref=ref, start=self._draft["preferred_time"], type="Beratung"),
                patient_id=card.id,
            )
            return StepOutcome(ok=True, note="appended to the sandbox calendar (claim)")

        return _book


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


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="appointment_set_details",
        description=(
            "Record or correct what the caller has said. Call it as soon as you learn a detail "
            "and again whenever they change one. Returns the details still missing."
        ),
        properties={
            "first_name": {"type": "string", "description": "The caller's first name."},
            "last_name": {"type": "string", "description": "The caller's last name."},
            "preferred_time": {
                "type": "string",
                "description": (
                    "The appointment start as YYYY-MM-DDTHH:MM in Europe/Berlin, resolved from "
                    "what the caller said. Omit it if you cannot resolve a day and minute."
                ),
            },
        },
        required=(),
        call=lambda session, args: session.set_details(
            first_name=args.get("first_name"),
            last_name=args.get("last_name"),
            preferred_time=args.get("preferred_time"),
        ),
    ),
    ToolSpec(
        name="appointment_find_slots",
        description=(
            "The times still bookable on one day. Call it whenever the caller asks what is free, "
            "or after a booking is refused because the time is taken. Returns 'slots'; an empty "
            "list carries a 'reason'. Offer only the times it returns."
        ),
        properties={
            "day": {
                "type": "string",
                "description": "The day to look at, as YYYY-MM-DD in Europe/Berlin.",
            }
        },
        required=("day",),
        call=lambda session, args: session.find_slots(str(args.get("day", ""))),
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
)
