"""The eval harness itself, exercised without reaching a model.

An eval suite is only worth having if its verdicts can be trusted, so the runner's own logic —
what it skips, what it injects, what it attributes to the agent — is tested here against a scripted
client. None of these tests need an API key, and none of them call one.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.workloads.conversational.booking import TOOLS
from app.workloads.conversational.evals.harness import VoiceScenario
from app.workloads.conversational.evals.runner import (
    AUDIO_ONLY,
    ScenarioRun,
    ToolCall,
    _openai_tools,
    language_for,
    run_scenario,
    snapshot,
)


def _message(text: str | None = None, calls: list[tuple[str, dict]] | None = None) -> Any:
    tool_calls = [
        SimpleNamespace(
            id=f"call-{index}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )
        for index, (name, args) in enumerate(calls or [])
    ]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=tool_calls or None))]
    )


class ScriptedClient:
    """Replays a fixed list of model replies, so a test pins agent behaviour exactly."""

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **_: Any) -> Any:
        return self._replies.pop(0)


def _scenario(scenario_id: str = "s", turns: tuple[str, ...] = ("hallo",), **kwargs: Any) -> VoiceScenario:
    return VoiceScenario(
        scenario_id=scenario_id,
        turns=turns,
        tags=frozenset(kwargs.pop("tags", ())),
        expected=("something",),
        environment=kwargs.pop("environment", {}),
    )


@pytest.mark.parametrize("key", sorted(AUDIO_ONLY))
def test_a_scenario_needing_audio_is_skipped_not_passed(key: str) -> None:
    """The failure mode that would make this whole suite a lie: counting a run that never ran."""
    run = run_scenario(_scenario(environment={key: 1}), ScriptedClient())

    assert run.skipped and key in run.skipped
    assert run.transcript == [] and run.tool_calls == []


def test_a_run_records_the_transcript_and_every_tool_call() -> None:
    client = ScriptedClient(
        _message(calls=[("appointment_set_details", {"first_name": "Anna"})]),
        _message("Und Ihr Nachname?"),
    )

    run = run_scenario(_scenario(turns=("Ich heiße Anna.",)), client)

    assert run.transcript == [
        {"role": "caller", "text": "Ich heiße Anna."},
        {"role": "agent", "text": "Und Ihr Nachname?"},
    ]
    assert [call.name for call in run.tool_calls] == ["appointment_set_details"]
    assert run.tool_calls[0].result["missing"] == ["last_name", "preferred_time"]


def test_a_write_that_is_acknowledged_but_never_persisted_is_not_a_booking() -> None:
    """The scenario the agent must never get wrong, injected end to end.

    The tool claims the calendar accepted the write and nothing lands. The governed postcondition
    re-queries and rejects, so the run has no booking for the agent to announce.
    """
    client = ScriptedClient(
        _message(
            calls=[
                (
                    "appointment_set_details",
                    {"first_name": "Anna", "last_name": "Weber",
                     "preferred_time": "2026-09-03T11:00"},
                )
            ]
        ),
        _message(calls=[("appointment_book", {})]),
        _message("Da bin ich mir nicht sicher."),
    )

    run = run_scenario(
        _scenario(
            environment={"tool": "appointment_book", "result": "write_acknowledged",
                         "postcondition": False}
        ),
        client,
    )

    book = [call for call in run.tool_calls if call.name == "appointment_book"][0]
    assert book.result["status"] == "rejected"
    assert not run.booked
    assert run.appointments == []


def test_an_appointment_seated_by_the_scenario_is_not_credited_to_the_agent() -> None:
    """Fault injection occupies slots. Counting those would report setup as the agent's doing."""
    client = ScriptedClient(
        _message(
            calls=[
                (
                    "appointment_set_details",
                    {"first_name": "Anna", "last_name": "Weber",
                     "preferred_time": "2026-09-03T11:00"},
                )
            ]
        ),
        _message(calls=[("appointment_book", {})]),
        _message("Der Termin ist leider vergeben."),
    )

    run = run_scenario(
        _scenario(environment={"tool": "appointment_book", "result": "slot_taken"}), client
    )

    assert run.appointments == []
    assert not run.booked
    assert run.invariants() == []


def test_an_unreachable_calendar_is_reported_as_unreachable_not_as_no_availability() -> None:
    client = ScriptedClient(
        _message(calls=[("appointment_find_slots", {"day": "2026-09-03"})]),
        _message("Ich erreiche den Kalender gerade nicht."),
    )

    run = run_scenario(
        _scenario(environment={"tool": "appointment_find_slots", "error": "timeout"}), client
    )

    assert run.tool_calls[0].result["slots"] == []
    assert "unreachable" in run.tool_calls[0].result["reason"]


def test_an_appointment_without_a_confirmed_booking_breaks_an_invariant() -> None:
    """Decidable failures are settled by the runner, never sent to a judge to have an opinion."""
    leaked = ScenarioRun("s", appointments=["2026-09-03T11:00"])
    doubled = ScenarioRun(
        "s",
        appointments=["2026-09-03T11:00", "2026-09-03T11:30"],
        tool_calls=[ToolCall("appointment_book", {}, {"status": "ok"})],
    )

    assert "without a booking that returned ok" in leaked.invariants()[0]
    assert "2 appointments" in doubled.invariants()[0]


def test_a_runaway_tool_loop_is_reported_rather_than_paid_for() -> None:
    client = ScriptedClient(*[_message(calls=[("appointment_find_slots", {"day": "2026-09-03"})])] * 9)

    run = run_scenario(_scenario(), client)

    assert run.error and "tool rounds" in run.error


@pytest.mark.parametrize(
    ("scenario_id", "environment", "expected"),
    [
        ("english-request", {}, "en"),
        ("arabic-time-request", {}, "ar"),
        ("de-time-only", {}, "de"),
        ("de-time-only", {"language": "ar"}, "ar"),
    ],
)
def test_a_scenario_runs_in_the_language_it_was_written_for(
    scenario_id: str, environment: dict, expected: str
) -> None:
    """Replaying an English scenario in a German session would test the no-switching rule instead."""
    assert language_for(_scenario(scenario_id, environment=environment)) == expected


def test_the_agent_is_offered_exactly_the_tools_the_live_caller_reaches() -> None:
    """One definition, two runtimes. A copy that drifted would make every result meaningless."""
    offered = _openai_tools()

    assert [tool["function"]["name"] for tool in offered] == [spec.name for spec in TOOLS]
    for tool, spec in zip(offered, TOOLS, strict=True):
        assert tool["function"]["description"] == spec.description
        assert tool["function"]["parameters"]["properties"] == spec.properties


def test_a_result_names_the_version_it_refers_to() -> None:
    """A pass means nothing without the snapshot it passed against."""
    identity = snapshot()

    assert identity["contract"] == "voice_appointment@1"
    assert len(identity["prompt_sha"]) == 12
    assert identity["tools"].split(",") == [spec.name for spec in TOOLS]
