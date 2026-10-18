"""A Studio agent can move and cancel by phone when its system can (ADR-0021).

Until 18 October 2026 `capabilities.resolve` withheld both tools from every configured agent, so a
published agent on Thevea could not change an appointment although the Studio said it could. The
decision now belongs to the connected system's `changes` capability.
"""

from __future__ import annotations

from dataclasses import replace

from app.agents.config import AgentConfig
from app.workloads.conversational.calendar import VOICE_CALENDARS, effective_config
from app.workloads.conversational.capabilities import resolve

_LIFECYCLE = {"cancel_appointment", "reschedule_appointment"}


def test_a_studio_agent_is_offered_moving_and_cancelling() -> None:
    assert _LIFECYCLE <= set(resolve(AgentConfig()).tools)


def test_on_thevea_a_live_call_keeps_them() -> None:
    config = effective_config(AgentConfig(), VOICE_CALENDARS["thevea"])

    assert _LIFECYCLE <= set(resolve(config).tools)


def test_a_system_that_books_but_cannot_change_takes_them_away() -> None:
    system = replace(VOICE_CALENDARS["thevea"], capabilities=frozenset({"availability", "patients", "booking"}))

    config = effective_config(AgentConfig(), system)

    assert "change_appointment" not in config.skills
    assert not _LIFECYCLE & set(resolve(config).tools)
    assert "appointment_book" in resolve(config).tools  # booking itself is untouched


def test_a_practice_that_switched_changes_off_keeps_them_off() -> None:
    """Taken away, never granted: Thevea's capability does not switch the skill back on."""
    config = AgentConfig(skills=["book_appointment", "practice_info"])

    assert not _LIFECYCLE & set(resolve(effective_config(config, VOICE_CALENDARS["thevea"])).tools)


def test_the_prompt_sends_changes_to_staff_only_without_the_tools() -> None:
    from app.workloads.conversational.surface import _instructions

    with_tools = _instructions("de", AgentConfig())
    without = _instructions("de", AgentConfig(skills=["book_appointment", "practice_info"]))

    assert "require a staff callback" not in with_tools
    assert "require a staff callback" in without
