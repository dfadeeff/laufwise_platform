"""Caller recall: what it may remember, what it may say, and what it must never unlock.

These are the tests that decide whether the `full` policy is defensible. Read
`test_recall_never_unlocks_a_change` first — if it ever goes red, recognising a phone number has
quietly become authentication.
"""

from __future__ import annotations

import pytest

from app.agents.config import AgentConfig
from app.config import settings
from app.memory.recall import caller_key, projection_is_recordable, recall_block
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.practice import load_practice
from app.providers.sandbox import SandboxCalendar


def _session() -> BookingSession:
    practice = load_practice()
    return BookingSession("call-1", calendar=SandboxCalendar(practice), practice=practice)


# --- what may be stored -------------------------------------------------------------------------


def test_nothing_is_remembered_about_a_caller_who_was_never_checked():
    """A binding between a phone number and a patient is only ever minted by a real date-of-birth
    check (ADR-0011 D5). A call that merely collected a name establishes nothing."""
    call = _session()
    call.set_details(first_name="Anna", last_name="Weber", date_of_birth="1971-04-12")

    assert call.memory_projection() is None
    assert projection_is_recordable(call.memory_projection()) is False


def test_an_ambiguous_match_is_not_an_identity():
    """Two records sharing a name and a birth date is the case the agent may not resolve on the
    phone — so it may not quietly resolve it into memory either."""
    call = _session()
    call.set_details(first_name="Anna", last_name="Weber", date_of_birth="1971-04-12")
    call._patient_checked = False  # what find_patient sets on an ambiguous result
    call._identity["patient_id"] = None

    assert call.memory_projection() is None


def test_what_is_remembered_carries_no_calendar_content():
    """ADR-0002 #11, enforced in code rather than promised in a docstring: memory holds the key,
    the calendar keeps the content."""
    call = _session()
    call.set_details(first_name="Anna", last_name="Weber", date_of_birth="1971-04-12")
    call._identity.update({"verified": True, "patient_id": 4711})

    projection = call.memory_projection()

    assert set(projection) == {"patient_id", "display_name", "last_outcome", "verified"}
    assert projection["display_name"] == "Weber"
    assert "1971-04-12" not in str(projection)  # the check is not stored beside the number
    for forbidden in ("start", "appointment", "service", "resource", "ref"):
        assert forbidden not in projection


# --- what must never be unlocked ----------------------------------------------------------------


def test_recall_never_unlocks_a_change():
    """The risk accepted under `full` is a greeting and one appointment time. The risk NOT
    accepted is that a phone number lets somebody move or cancel an appointment.

    There is deliberately no way to hand a remembered identity to the session as *verified*, so
    the strongest thing a recalled call can do is what any unverified call can do — and the
    change paths still refuse it.
    """
    call = _session()
    call._identity["patient_id"] = 4711  # the most a recalled call could ever pre-load

    assert call._identity["verified"] is False
    assert call.cancel()["status"] != "ok"
    assert call.reschedule()["status"] != "ok"


def test_there_is_no_method_for_handing_memory_an_identity():
    """Pre-loading a remembered patient id would also silence the note that tells the agent to run
    find_patient before booking — the note that stopped a duplicate patient record five runs out
    of five. The absence of the method is the guarantee."""
    assert not hasattr(BookingSession, "hint_identity")


# --- what may be said ---------------------------------------------------------------------------


def test_the_greeting_policy_never_mentions_an_appointment():
    """Under `greeting` the appointment never enters the model's context, so there is nothing for
    it to disclose. That is the same mechanism as withholding a tool, not a prompt asking nicely."""
    block = recall_block(policy="greeting", display_name="Müller", next_start="2026-09-24T09:20")

    assert "Müller" in block
    assert "09:20" not in block
    assert "2026-09-24" not in block


def test_the_full_policy_states_one_appointment_and_still_demands_verification():
    block = recall_block(policy="full", display_name="Müller", next_start="Dienstag 09:20")

    assert "Dienstag 09:20" in block
    assert "date of birth" in block
    assert "not proof" in block


def test_a_caller_we_know_nothing_about_produces_no_block():
    assert recall_block(policy="full", display_name=None) is None
    assert recall_block(policy="off", display_name="Müller") is None


# --- the pseudonym ------------------------------------------------------------------------------


def test_without_a_pepper_nothing_can_be_remembered(monkeypatch: pytest.MonkeyPatch):
    """An unsalted hash of a phone number is a lookup table anyone holding it can reverse, so a
    missing pepper switches recall off everywhere rather than degrading quietly."""
    monkeypatch.setattr(settings, "caller_memory_pepper", None)

    assert caller_key("tenant-a", "+4930555123") is None


def test_the_same_number_is_a_different_caller_in_another_practice(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "caller_memory_pepper", "test-pepper")

    assert caller_key("tenant-a", "+4930555123") != caller_key("tenant-b", "+4930555123")
    assert caller_key("tenant-a", "+4930555123") == caller_key("tenant-a", "+49 30 555 123")


def test_an_unusable_number_is_not_a_key(monkeypatch: pytest.MonkeyPatch):
    """Withheld, malformed or anonymous callers simply have no memory."""
    monkeypatch.setattr(settings, "caller_memory_pepper", "test-pepper")

    assert caller_key("tenant-a", "anonymous") is None
    assert caller_key("tenant-a", "") is None


# --- the gate -----------------------------------------------------------------------------------


def test_full_recall_cannot_be_published_without_an_explicit_acknowledgement():
    """`full` is opt-in knowingly, and the acknowledgement is stored in the revision — so an audit
    shows who accepted it and when (ADR-0011 D5)."""
    unacknowledged = AgentConfig(recall_policy="full")
    acknowledged = AgentConfig(recall_policy="full", recall_acknowledged=True)

    assert any("explicit confirmation" in issue for issue in unacknowledged.publish_issues())
    assert not any("explicit confirmation" in issue for issue in acknowledged.publish_issues())


def test_an_agent_published_before_recall_existed_remembers_nobody():
    assert AgentConfig.model_validate({"name": "Empfang"}).recall_policy == "off"


# --- shadow calls ---------------------------------------------------------------------------


def test_a_cohort_reads_a_call_the_way_the_platform_recorded_it():
    """The comparison must be computable from rows an ordinary call already leaves behind —
    otherwise it only works for calls somebody remembered to set up as an experiment."""
    from types import SimpleNamespace
    from datetime import datetime, timedelta, timezone

    from app.workloads.conversational.shadow import Cohort

    started = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
    call = SimpleNamespace(
        started_at=started,
        ended_at=started + timedelta(seconds=95),
        events=[
            SimpleNamespace(kind="turn", payload={"role": "caller", "text": "Guten Tag"}),
            SimpleNamespace(kind="turn", payload={"role": "agent", "text": "Guten Tag"}),
            SimpleNamespace(kind="tool_call", payload={"tool": "x", "duration_ms": 310}),
            SimpleNamespace(kind="tool_call", payload={"tool": "y", "duration_ms": 1900}),
            SimpleNamespace(kind="call_summary", payload={"summary": {"outcome": "TERMIN GEBUCHT"}}),
        ],
    )

    cohort = Cohort("realtime")
    cohort.add(call)
    report = cohort.report()

    assert (report["calls"], report["unfinished"]) == (1, 0)
    assert report["median_seconds"] == 95.0
    assert (report["median_caller_turns"], report["median_agent_turns"]) == (1, 1)
    assert report["tool_ms_median"] == 1105.0 and report["tool_ms_p95"] == 1900
    assert report["outcomes"] == {"TERMIN GEBUCHT": 1}


def test_a_call_the_pipeline_never_closed_is_counted_not_hidden():
    """It is the failure a new engine is most likely to introduce, so it must not vanish into a
    median."""
    from types import SimpleNamespace
    from datetime import datetime, timezone

    from app.workloads.conversational.shadow import Cohort

    cohort = Cohort("realtime")
    cohort.add(
        SimpleNamespace(
            started_at=datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc), ended_at=None, events=[]
        )
    )

    assert cohort.report()["unfinished"] == 1
    assert cohort.report()["median_seconds"] is None
