"""The voice agent books, moves and cancels only through the governed loop.

These test the guarantee rather than the prompt. The prompt asks for six details, an explicit
confirmation, a verified identity and the approved short-notice wording; these prove that an agent
which forgets to ask — or decides to be helpful and skip one — still cannot produce, move or
cancel an appointment.

Numbered references are to the practice specification's acceptance tests (§8).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from laufwise.adapters.base import StepOutcome

from app.connectors.base import Appointment, AppointmentLifecycle, DestinationCalendar
from app.control_plane.runner import execute_contract
from app.providers.sandbox import BOOKED, CANCELLED, SandboxCalendar, SandboxStateProvider
from app.templates.loader import load_template
from app.workloads.conversational.booking import (
    CONTRACT_PATH,
    OFFERED_SLOTS,
    OUTCOME_BOOKED,
    OUTCOME_CALLBACK,
    OUTCOME_CANCELLED,
    OUTCOME_INCOMPLETE,
    OUTCOME_INFO,
    OUTCOME_MOVED,
    BookingSession,
    normalize_birthdate,
    normalize_phone,
)
from app.workloads.conversational.practice import load_practice
from app.workloads.conversational.surface import _instructions


def _next_weekday(offset_days: int = 1) -> date:
    """The soonest open day at least `offset_days` out, so the suite never asks about a weekend."""
    schedule = load_practice().schedule
    day = date.today() + timedelta(days=offset_days)
    while not schedule.is_open(day):
        day += timedelta(days=1)
    return day


def _slot(offset_days: int = 7) -> str:
    return f"{_next_weekday(offset_days).isoformat()}T09:00"


def _complete(when: str | None = None) -> dict[str, str]:
    return {
        "first_name": "Anna",
        "last_name": "Weber",
        "date_of_birth": "1971-04-12",
        "phone": "0176 4289 9911",
        "service_key": "medizinische_fusspflege",
        "preferred_time": when or _slot(),
    }


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BookingSession:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    return BookingSession("test-session")


def _ready(session: BookingSession, **overrides: str) -> None:
    """Collect every detail, run the duplicate check, and confirm.

    All three are required by the contract (v3), so this is the full state a booking is allowed to
    run from — `find_patient` included, which is exactly the step the evals caught the agent
    skipping.
    """
    session.set_details(**{**_complete(), **overrides})
    session.find_patient()
    session.confirm()


# --- booking: every required detail is a gate (§8.1) ---


@pytest.mark.parametrize(
    ("withheld", "named"),
    [
        ("first_name", "first name"),
        ("last_name", "last name"),
        ("date_of_birth", "date of birth"),
        ("phone", "phone number"),
        ("service_key", "no treatment has been chosen"),
        ("preferred_time", "appointment time"),
    ],
)
def test_booking_blocks_until_every_required_detail_is_collected(
    session: BookingSession, withheld: str, named: str
) -> None:
    """Each missing detail blocks the write AND names itself, so the agent knows what to ask."""
    session.set_details(**{k: v for k, v in _complete().items() if k != withheld})
    session.confirm()

    result = session.book()

    assert result["status"] == "blocked"
    assert named in result["reason"]
    assert result["missing"] == [withheld]
    assert result["appointment"] is None


def test_an_unconfirmed_booking_is_blocked_even_with_every_detail(session: BookingSession) -> None:
    """Spec §3.5 condition 1 and §3.3: the patient confirms aloud and is told how their data is
    handled, or nothing is created.

    `confirm()` supplies both — the read-back and the data-processing notice are one turn of a
    phone call — so a booking that skipped it fails on whichever of the two gates comes first.
    Either reason is the same finding: the confirmation step did not happen.
    """
    session.set_details(**_complete())

    result = session.book()

    assert result["status"] == "blocked"
    assert result["reason"] in {
        "the patient has not been told how their data is processed",
        "the patient has not confirmed the name, date, time and address aloud",
    }


def test_a_correction_invalidates_an_earlier_confirmation(session: BookingSession) -> None:
    """A yes is a yes to particular details. Change one and it stops counting (§8.6)."""
    _ready(session)
    assert session.confirmed

    state = session.set_details(date_of_birth="1971-04-21")

    assert not session.confirmed
    assert state["confirmation_required"] is True
    assert session.book()["status"] == "blocked"


def test_complete_confirmed_details_book_and_are_verified_by_the_calendar(
    session: BookingSession,
) -> None:
    _ready(session)

    result = session.book()

    assert result["status"] == "ok"
    assert result["appointment"]["patient"] == "Anna Weber"
    assert result["appointment"]["address"] == load_practice().address
    assert session.outcome() == OUTCOME_BOOKED


def test_booking_the_same_details_twice_creates_one_appointment(session: BookingSession) -> None:
    """A dropped connection or a caller asking "did that go through?" must not double-book (§8.8)."""
    _ready(session)

    first, second = session.book(), session.book()

    assert (first["status"], second["status"]) == ("ok", "ok")
    assert len(session.calendar.appointments) == 1


def test_a_tool_that_claims_success_without_writing_is_rejected(tmp_path: Path) -> None:
    """The governance property the whole design rests on.

    The postcondition re-queries the calendar, so the tool's own return value cannot decide the
    outcome. Without this, an agent could tell a caller their appointment is booked on the
    strength of nothing at all.
    """
    calendar = SandboxCalendar()
    draft = {
        **_complete(),
        "resource": "MA1",
        "confirmed": True,
        "consent_policy_id": "p",
        "patient_checked": True,
    }

    result = execute_contract(
        load_template(CONTRACT_PATH),
        case={},
        runs_dir=tmp_path,
        real_providers={"sandbox": SandboxStateProvider(calendar, draft, "ref-1")},
        extra_tools={"book_appointment": lambda provider, step: StepOutcome(ok=True, note="lied")},
    )

    assert result.status == "rejected"
    assert "did not record the appointment" in result.steps[0].reason
    assert calendar.find_appointment("ref-1") is None


# --- availability: only MA1/MA2/MA3, never the break (§8.19) ---


def test_availability_never_offers_the_midday_break() -> None:
    """The break is outside every opening period, so no slot inside it can be produced at all."""
    calendar = SandboxCalendar()
    day = _next_weekday(7)

    slots = calendar.free_slots(
        date_from=day, date_to=day, limit=99, now=datetime.combine(day, time(0, 1))
    )

    assert slots
    assert all(not slot.start.endswith(("T12:00", "T12:30")) for slot in slots)
    assert {slot.resource for slot in slots} <= set(calendar.schedule.resources)


def test_a_time_inside_the_break_cannot_be_booked_even_when_asked_for_directly(
    session: BookingSession,
) -> None:
    """Availability search is not the only route to a booking; the grid check is."""
    state = session.set_details(preferred_time=f"{_next_weekday(7).isoformat()}T12:15")

    assert "preferred_time" in state["rejected"]
    assert "no appointment slot at that time" in state["rejected"]["preferred_time"]


def test_availability_is_capped_at_three(session: BookingSession) -> None:
    """A phone caller cannot hold a longer list in their head (spec §3.1)."""
    result = session.search_availability(date_from=_next_weekday(7).isoformat())

    assert len(result["slots"]) == OFFERED_SLOTS
    assert "reason" not in result


def test_a_morning_search_returns_only_morning_slots(session: BookingSession) -> None:
    """The windows are configuration, not prompt text — so they are checkable (§8.3)."""
    result = session.search_availability(
        date_from=_next_weekday(7).isoformat(), time_window="morning"
    )

    morning = load_practice().window("morning")
    assert result["slots"]
    for slot in result["slots"]:
        assert morning.start <= time.fromisoformat(slot["start"][11:]) < morning.end


def test_an_exact_range_search_honours_both_bounds(session: BookingSession) -> None:
    """"After half four, any day" (spec §4.3)."""
    result = session.search_availability(
        date_from=_next_weekday(7).isoformat(),
        time_window="exact_range",
        exact_time_from="16:30",
        exact_time_to="18:00",
    )

    assert result["slots"]
    assert all(slot["start"][11:] >= "16:30" for slot in result["slots"])


def test_a_weekday_preference_is_honoured(session: BookingSession) -> None:
    """"Tuesday afternoon" (spec §4.3)."""
    result = session.search_availability(preferred_days=["tuesday"], time_window="afternoon")

    assert result["slots"]
    assert all(
        date.fromisoformat(slot["start"][:10]).weekday() == 1 for slot in result["slots"]
    )


def test_an_open_ended_search_answers_rather_than_asking_back(session: BookingSession) -> None:
    """"When is your next available appointment?" is a search, not a question to bounce back."""
    result = session.search_availability()

    assert result["slots"]


def test_a_closed_range_says_it_is_closed_rather_than_returning_a_bare_empty_list(
    session: BookingSession,
) -> None:
    """Closed, past and full are different sentences; an empty list alone invites a guess."""
    saturday = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
    sunday = saturday + timedelta(days=1)

    result = session.search_availability(
        date_from=saturday.isoformat(), date_to=sunday.isoformat()
    )

    assert result["slots"] == []
    assert "closed" in result["reason"]


def test_past_slots_are_never_offered_for_today() -> None:
    """A time the caller cannot take is a false offer, not a harmless one."""
    calendar = SandboxCalendar()
    day = _next_weekday()
    midday = datetime.combine(day, time(12, 15))

    slots = calendar.free_slots(date_from=day, date_to=day, limit=OFFERED_SLOTS, now=midday)

    assert slots and all(slot.start > midday.strftime("%Y-%m-%dT%H:%M") for slot in slots)


def test_an_offer_is_not_a_reservation(session: BookingSession) -> None:
    """Availability is read outside the governed loop, so the slot is re-checked when it books.

    Between offering a time and booking it, someone else can take it — on every calendar. The
    precondition is what catches that; the offer itself reserves nothing (§8.7).
    """
    offered = session.search_availability(date_from=_next_weekday(7).isoformat())["slots"][0]
    # Recorded and confirmed while the slot is still free — the caller chose a real time. Only
    # THEN does someone else take it, on every calendar, which is what the precondition must
    # catch: the draft-level check already passed and cannot be asked again.
    _ready(session, preferred_time=offered["start"])
    for resource in session.calendar.schedule.resources:
        session.calendar.create_appointment(
            Appointment(
                ref=f"someone-else-{resource}",
                start=offered["start"],
                raw={"resource": resource},
            ),
            patient_id=1,
        )

    result = session.book()

    assert result["status"] == "blocked"
    assert "just been taken" in result["reason"]


# --- patients: matched, never guessed (§8.4, §8.5) ---


def test_an_existing_patient_is_reused_rather_than_duplicated(session: BookingSession) -> None:
    _ready(session)
    assert session.book()["status"] == "ok"

    second = BookingSession("second-call", calendar=session.calendar)
    second.set_details(**{**_complete(_slot(8)), "first_name": "Anna", "last_name": "Weber"})

    assert second.find_patient()["result"] == "unique_match"
    second.confirm()  # the check above is what the contract requires before a booking
    assert second.book()["status"] == "ok"
    assert len(session.calendar.match_patients("Anna", "Weber", "1971-04-12")) == 1


def test_two_patients_sharing_a_name_and_birthday_are_ambiguous_and_disclose_nothing(
    session: BookingSession,
) -> None:
    """Spec §3.2: never pick between records, and never leak from either (§8.5)."""
    from app.connectors.base import Patient

    for _ in range(2):
        session.calendar.create_patient(
            Patient(vorname="Anna", nachname="Weber", geburtsdatum="1971-04-12")
        )
    session.set_details(**_complete())

    result = session.find_patient()

    assert result["result"] == "ambiguous"
    assert "patient_id" not in result


def test_a_name_without_a_birth_date_never_matches(session: BookingSession) -> None:
    """Matching on a name alone is how two different people become one patient card."""
    session.set_details(first_name="Anna", last_name="Weber")

    assert session.find_patient()["result"] == "none"


def test_umlaut_and_spacing_variants_are_the_same_person(session: BookingSession) -> None:
    from app.connectors.base import Patient

    session.calendar.create_patient(
        Patient(vorname="Anna-Maria", nachname="Müller", geburtsdatum="1971-04-12")
    )

    assert session.calendar.match_patients("anna maria", "Mueller", "1971-04-12")


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [("0176 4289 9911", "+4917642899911"), ("0049 89 41115335", "+498941115335"), ("hallo", None)],
)
def test_a_phone_number_is_normalized_or_refused(spoken: str, expected: str | None) -> None:
    assert normalize_phone(spoken) == expected


@pytest.mark.parametrize("value", ["2999-01-01", "12.04.1971", "not a date", ""])
def test_an_implausible_birth_date_is_refused(value: str) -> None:
    """A misheard year is the commonest way a caller is matched to the wrong card (§8.6)."""
    assert normalize_birthdate(value) is None


# --- existing appointments: verification gates every disclosure (§8.21) ---


def _booked_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BookingSession:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("existing-call")
    _ready(session)
    assert session.book()["status"] == "ok"
    return session


def test_a_failed_verification_discloses_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the date, not the treatment, not whether any appointment exists (spec §3.4)."""
    booked = _booked_session(tmp_path, monkeypatch)
    caller = BookingSession("other-call", calendar=booked.calendar)

    result = caller.get_patient_appointments(
        first_name="Anna",
        last_name="Weber",
        date_of_birth="1971-04-12",
        appointment_date=_next_weekday(7).isoformat(),
        appointment_time="15:30",  # not the appointment they hold
    )

    assert result["verified"] is False
    assert result["reason"] == "could not be verified — offer a callback"
    # Nothing about the appointment leaks through the note either — it is an instruction to the
    # agent, and an instruction that quoted the date would defeat the check it enforces.
    assert "appointment" not in result
    assert "Tell the caller NOTHING" in result["agent_notes"][0]


def test_verification_succeeds_from_a_different_caller_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A patient may ring from any phone; the number is not part of the identity check (§8.21)."""
    booked = _booked_session(tmp_path, monkeypatch)
    caller = BookingSession("other-call", calendar=booked.calendar)

    result = caller.get_patient_appointments(
        first_name="Anna",
        last_name="Weber",
        date_of_birth="1971-04-12",
        appointment_date=_slot()[:10],
        appointment_time="09:00",
    )

    assert result["verified"] is True
    assert result["appointment"]["start"] == _slot()


# --- cancellation: a status, never a deletion (§8.10, §8.11) ---


def test_cancelling_without_verification_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booked = _booked_session(tmp_path, monkeypatch)
    caller = BookingSession("other-call", calendar=booked.calendar)

    result = caller.cancel()

    assert result["status"] == "blocked"
    assert "verify" in result["reason"]


def _verified(booked: BookingSession) -> BookingSession:
    caller = BookingSession("cancel-call", calendar=booked.calendar)
    caller.get_patient_appointments(
        first_name="Anna",
        last_name="Weber",
        date_of_birth="1971-04-12",
        appointment_date=_slot()[:10],
        appointment_time="09:00",
    )
    return caller


def test_cancelling_is_blocked_until_a_move_has_been_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §4.5 #2 is a gate, not a habit the prompt hopes for (§8.11)."""
    caller = _verified(_booked_session(tmp_path, monkeypatch))
    caller.confirm()

    result = caller.cancel()

    assert result["status"] == "blocked"
    assert "offered a move" in result["reason"]


def test_a_cancellation_sets_the_status_and_keeps_the_record_and_its_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Preserving the appointment and its history; do not permanently delete it" (spec §4.5 #7)."""
    booked = _booked_session(tmp_path, monkeypatch)
    caller = _verified(booked)
    caller.change_notices("cancel")
    caller.confirm()

    result = caller.cancel(reason="im Urlaub")

    assert result["status"] == "ok"
    ref = booked.booked_ref
    assert booked.calendar.status_of(ref) == CANCELLED
    assert booked.calendar.find_appointment(ref) is not None
    assert [entry["event"] for entry in booked.calendar.history_of(ref)] == [
        "created",
        "cancelled",
    ]
    assert caller.outcome() == OUTCOME_CANCELLED


def test_a_short_notice_change_carries_the_approved_ausfallhonorar_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent is handed the practice's exact sentence, not asked to compose one (§8.10)."""
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("short-notice")
    soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    session.calendar.create_appointment(
        Appointment(ref="soon", start=soon, raw={"resource": "MA1"}), patient_id=1
    )
    session._identity.update({"verified": True, "target_ref": "soon"})

    notices = session.change_notices("cancel")

    assert notices["short_notice"] is True
    assert load_practice().phrases["ausfallhonorar"] in notices["say"]
    assert load_practice().phrases["offer_reschedule"] in notices["say"]


# --- rescheduling: one appointment moves, atomically (§8.9) ---


def test_a_move_relocates_the_same_appointment_without_creating_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    booked = _booked_session(tmp_path, monkeypatch)
    caller = _verified(booked)
    caller.change_notices("reschedule")
    new_time = _slot(8)
    caller.set_details(preferred_time=new_time)
    caller.confirm()

    result = caller.reschedule()

    assert result["status"] == "ok"
    assert len(booked.calendar.appointments) == 1
    moved = booked.calendar.find_appointment(booked.booked_ref)
    assert moved.start == new_time
    assert moved.raw["status"] == BOOKED
    assert caller.outcome() == OUTCOME_MOVED


def test_a_move_into_a_taken_slot_leaves_the_original_appointment_standing() -> None:
    """Atomic (spec §3.6): a caller must never lose the appointment they had."""
    calendar = SandboxCalendar()
    original, wanted = _slot(), _slot(8)
    calendar.create_appointment(
        Appointment(ref="mine", start=original, raw={"resource": "MA1"}), patient_id=1
    )
    calendar.create_appointment(
        Appointment(ref="theirs", start=wanted, raw={"resource": "MA1"}), patient_id=2
    )

    moved = calendar.reschedule_appointment("mine", new_start=wanted, new_resource="MA1")

    assert moved is False
    assert calendar.find_appointment("mine").start == original
    assert calendar.status_of("mine") == BOOKED


# --- callbacks and outcomes (§8.13, §8.14, §8.15, §8.16) ---


def test_a_callback_request_needs_a_number_and_records_the_caller_words(
    session: BookingSession,
) -> None:
    assert session.create_callback_request(reason="fragt nach einem Menschen")["status"] == (
        "needs_phone"
    )

    session.set_details(phone="0176 4289 9911")
    result = session.create_callback_request(
        reason="fragt nach einem Menschen", urgency="urgent_review"
    )

    assert result["status"] == "recorded"
    assert result["phone"] == "+4917642899911"
    assert result["urgency"] == "urgent_review"
    assert session.outcome() == OUTCOME_CALLBACK


def test_a_service_the_agent_may_not_choose_is_refused_with_a_reason(
    session: BookingSession,
) -> None:
    """Packages, braces and home visits are handoffs, not bookings (spec §5)."""
    state = session.set_details(service_key="kaltplasma_6")

    assert "cannot be booked by phone" in state["rejected"]["service_key"]
    assert session.draft["service_key"] == ""


@pytest.mark.parametrize(
    ("turns", "expected"),
    [(0, OUTCOME_INCOMPLETE), (1, OUTCOME_INCOMPLETE), (4, OUTCOME_INFO)],
)
def test_a_call_that_changed_nothing_is_classified_by_what_happened_in_it(
    session: BookingSession, turns: int, expected: str
) -> None:
    """A question answered and a caller who rang off are different lines in the practice's inbox."""
    session.caller_turns = turns

    assert session.outcome() == expected


def test_a_dropped_call_mid_booking_is_reported_as_incomplete(session: BookingSession) -> None:
    """§8.15: the call ends after the phone number and before anything is booked."""
    session.set_details(first_name="Anna", phone="0176 4289 9911")
    session.caller_turns = 5

    assert session.outcome() == OUTCOME_INCOMPLETE
    assert session.summary()["staff_action_required"] is True


# --- the seams stay sealed ---


def test_the_calendar_seam_still_offers_no_generic_mutation() -> None:
    """ADR-0008 adds two NAMED transitions and nothing else.

    Append-only is enforced by absence (ADR-0004 D7). Cancel and reschedule are now present and
    deliberately so; `update_appointment` and `delete_appointment` must stay unrepresentable, or
    the guarantee is gone for every connector.
    """
    calendar = SandboxCalendar()

    assert isinstance(calendar, DestinationCalendar)
    assert isinstance(calendar, AppointmentLifecycle)
    for forbidden in ("update_appointment", "delete_appointment", "update_patient"):
        assert not hasattr(SandboxCalendar, forbidden)


def test_the_prompt_asks_for_exactly_the_details_the_contract_enforces() -> None:
    """The prompt is the hint and the contract is the guarantee; they must at least agree."""
    prompt = _instructions("de")

    assert "{{" not in prompt
    for detail in ("first name", "last name", "date of birth", "phone number", "treatment"):
        assert detail in prompt.lower()


def test_the_prompt_carries_the_practice_facts_from_configuration_not_prose() -> None:
    """Spec §5: prices and hours are config, rendered in — not written into the prompt."""
    practice = load_practice()
    prompt = _instructions("de")

    assert practice.address in prompt
    assert "€69" in prompt
    assert practice.phrases["muster13"] in prompt
    assert "12:00–13:00 break" in prompt


def test_repeating_a_move_that_already_landed_reports_it_rather_than_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection during a move must not look like "that time is taken" (§8.8).

    The appointment now sitting at the new time is the very one being moved, so the slot check
    has to recognise itself — otherwise the second attempt blocks on the first attempt's success.
    """
    booked = _booked_session(tmp_path, monkeypatch)
    caller = _verified(booked)
    caller.change_notices("reschedule")
    caller.set_details(preferred_time=_slot(8))
    caller.confirm()

    first, second = caller.reschedule(), caller.reschedule()

    assert (first["status"], second["status"]) == ("ok", "ok")
    assert len(booked.calendar.appointments) == 1


# --- agent_notes: the platform tells the agent what to do next (Wonderful house pattern) ---


def test_every_tool_result_carries_next_step_guidance() -> None:
    """A rule that lives only in the prompt competes with the whole prompt, and loses.

    Observed before this existed: the agent collected a name and a birth date and booked without
    ever checking for an existing record, in five eval runs out of five, with the rule sitting in
    the prompt the entire time. The note arrives attached to the result that hands it the name.
    """
    from app.workloads.conversational.booking import TOOLS

    arguments = {
        "action": "cancel",
        "reason": "x",
        "first_name": "",
        "last_name": "",
        "date_of_birth": "",
        "appointment_date": "",
        "appointment_time": "",
    }
    for spec in TOOLS:
        result = spec.call(BookingSession(f"notes-{spec.name}"), dict(arguments))
        assert result.get("agent_notes"), f"{spec.name} returned no agent_notes"
        assert all(isinstance(note, str) and note for note in result["agent_notes"])


def test_completing_the_identity_details_asks_for_the_record_check(
    session: BookingSession,
) -> None:
    """The note that closes the gap acc-04 found: it fires the moment the name and DOB are in."""
    session.set_details(preferred_time=_slot(), service_key="medizinische_fusspflege", phone="0176 4289 9911")

    state = session.set_details(first_name="Anna", last_name="Weber", date_of_birth="1971-04-12")

    assert any("find_patient" in note for note in state["agent_notes"])


def test_a_rejected_detail_is_named_in_the_guidance_rather_than_passed_over(
    session: BookingSession,
) -> None:
    """acc-05: an implausible birth date must produce an instruction to ask again."""
    state = session.set_details(date_of_birth="2090-01-01")

    assert "date_of_birth" in state["rejected"]
    assert any("ask again" in note.lower() for note in state["agent_notes"])


def test_the_record_check_reopens_when_the_name_is_corrected(session: BookingSession) -> None:
    """A different person is a different lookup; keeping the old verdict binds the wrong card."""
    _ready(session)
    session.find_patient()

    state = session.set_details(last_name="Weberova")

    assert any("find_patient" in note for note in state["agent_notes"])


def test_a_refusal_never_tells_the_agent_something_happened(session: BookingSession) -> None:
    """Every blocked write says so in words the agent can read straight out."""
    session.set_details(**{k: v for k, v in _complete().items() if k != "phone"})
    session.confirm()

    result = session.book()

    assert result["status"] == "blocked"
    assert any("do NOT tell the caller" in note.lower() or "Do NOT" in note for note in result["agent_notes"])
    assert any("phone" in note for note in result["agent_notes"])


# --- skills: the split is a boundary, not a filing system ---


def test_every_tool_belongs_to_a_skill_or_is_global() -> None:
    """A tool no skill claims is unreachable. A skill naming a tool that does not exist is a
    manifest that lies — both would make the allowlist decorative."""
    from app.workloads.conversational.booking import TOOLS
    from app.workloads.conversational.skills import GLOBAL_TOOLS, allowed_tools, load_skills

    implemented = {spec.name for spec in TOOLS}
    claimed = set(allowed_tools())

    assert claimed <= implemented, f"skills name tools that do not exist: {claimed - implemented}"
    assert implemented <= claimed, f"tools no skill offers: {implemented - claimed}"
    for skill in load_skills():
        assert skill.tools or "read_only" in skill.tags, f"{skill.name} claims no tools"
        assert not set(skill.tools) & set(GLOBAL_TOOLS), f"{skill.name} re-declares a global tool"


def test_the_state_changing_skills_are_the_ones_that_write() -> None:
    """Read-only and state-changing flows are tagged apart so the risky half can carry stricter
    review and its own evals. A skill that writes without the tag would slip that net."""
    from app.workloads.conversational.skills import load_skills

    writes = {"appointment_book", "cancel_appointment", "reschedule_appointment"}
    for skill in load_skills():
        assert bool(set(skill.tools) & writes) == skill.is_state_changing, skill.name


def test_the_assembled_prompt_carries_the_base_and_every_skill() -> None:
    from app.workloads.conversational.skills import load_skills

    prompt = _instructions("de")

    assert "{{" not in prompt
    for skill in load_skills():
        assert skill.display_name in prompt, f"{skill.name} is not routed to"
        assert skill.prompt.splitlines()[0].lstrip("# ") in prompt


def test_a_skill_prompt_stays_short_enough_to_review() -> None:
    """Longer than a page and it is two skills wearing one name."""
    from app.workloads.conversational.skills import load_skills

    for skill in load_skills():
        assert len(skill.prompt.splitlines()) <= 60, f"{skill.name} is too big — split it"


def test_booking_is_blocked_until_the_duplicate_check_has_run(session: BookingSession) -> None:
    """Spec §3.2: "Do not create a duplicate until the search has been completed."

    Version 2 said this in the prompt and the agent skipped it in five eval runs out of five,
    opening a second card for a patient the practice already had. Version 3 makes it a gate.
    """
    session.set_details(**_complete())
    session.confirm()

    result = session.book()

    assert result["status"] == "blocked"
    assert "already has a record" in result["reason"]


def test_an_ambiguous_record_does_not_unlock_the_booking(session: BookingSession) -> None:
    """The one case that must reach a human must not be the one case that satisfies the check."""
    from app.connectors.base import Patient

    for _ in range(2):
        session.calendar.create_patient(
            Patient(vorname="Anna", nachname="Weber", geburtsdatum="1971-04-12")
        )
    session.set_details(**_complete())
    assert session.find_patient()["result"] == "ambiguous"
    session.confirm()

    result = session.book()

    assert result["status"] == "blocked"
    assert "already has a record" in result["reason"]
    assert session.calendar.find_appointment(session.booked_ref or "") is None


@pytest.mark.parametrize(
    ("field", "value", "cue"),
    [("phone", "0176 4289 9911", "number back"), ("date_of_birth", "1971-04-12", "on its own")],
)
def test_the_two_details_that_must_be_read_back_ask_for_it_when_recorded(
    session: BookingSession, field: str, value: str, cue: str
) -> None:
    """Spec §4.2 step 7 singles out the phone number and the date of birth.

    They are the two the practice cannot recover from being wrong — a wrong number is a callback
    that never arrives, a wrong birth date is the wrong patient card — and the note fires on the
    result that just accepted the value, which is the one moment reading it back is a natural
    sentence rather than an interruption.
    """
    notes = session.set_details(**{field: value})["agent_notes"]

    assert any(cue in note for note in notes)


def test_the_read_back_is_asked_for_once_not_on_every_correction(
    session: BookingSession,
) -> None:
    """Repeating the demand makes the agent ask "is that right?" twice in a row.

    Observed eating two turns of a call, so it never reached the booking at all — the note has to
    prompt a confirmation, not a loop.
    """
    first = session.set_details(phone="0176 4289 9911")["agent_notes"]
    again = session.set_details(phone="0176 4289 9912")["agent_notes"]

    assert any("number back" in note for note in first)
    assert not any("number back" in note for note in again)
