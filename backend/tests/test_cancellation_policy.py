"""The practice's own cancellation window and wording (ADR-0021).

Inside the window nothing is changed by phone: the caller hears the practice's sentence and staff
call back. Configuration, not code, so another practice sets its own hours and words.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.agents.config import AgentConfig
from app.connectors.base import Appointment
from app.workloads.conversational.booking import BookingSession

POLICY = "Absagen sind bis 48 Stunden vor dem Termin kostenfrei."


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _session(hours_away: float, **config) -> BookingSession:
    session = BookingSession("policy", practice=AgentConfig(**config).to_practice())
    start = (datetime.now() + timedelta(hours=hours_away)).strftime("%Y-%m-%dT%H:%M")
    session.calendar.create_appointment(
        Appointment(ref="a", start=start, raw={"resource": "MA1"}), patient_id=1
    )
    session._identity.update({"verified": True, "target_ref": "a"})
    return session


def test_the_practice_s_window_and_words_are_what_the_caller_hears() -> None:
    session = _session(30, cancellation_free_hours=48, cancellation_policy=POLICY)

    result = session.change_notices("cancel")

    assert result["status"] == "blocked" and result["say"] == [POLICY]
    assert "48 hours" in result["reason"]


def test_outside_the_window_the_change_goes_ahead() -> None:
    session = _session(30, cancellation_free_hours=24, cancellation_policy=POLICY)

    assert "status" not in session.change_notices("cancel")


def test_an_agent_without_a_policy_keeps_24_hours_and_the_neutral_sentence() -> None:
    practice = AgentConfig().to_practice()

    assert practice.policy.short_notice_hours == 24
    assert practice.phrases["ausfallhonorar"] == "The practice will explain any cancellation charges."
