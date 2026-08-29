"""A live call is written down, and writing it down can never take the call out.

The Postgres round trip lives in `test_persistence.py` (it skips when the DB is unreachable).
What is here is the logic that decides WHAT gets recorded and what happens when recording fails —
neither of which needs a database, and both of which are where the damage would be.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import FramePushed

from app.schemas.conversation import _outcome
from app.workloads.conversational import recording
from app.workloads.conversational.evals.runner import compare, write_report
from app.workloads.conversational.recording import ConversationRecorder
from app.workloads.conversational.surface import _TranscriptObserver


class _Recorded(ConversationRecorder):
    """Captures events instead of writing them, so the observer can be tested on its own."""

    def __init__(self) -> None:
        super().__init__(uuid.uuid4())
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def _append(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))


def _run(coro: Any) -> Any:
    """Drive one coroutine to completion. The suite has no pytest-asyncio; persistence tests do
    the same thing, so this stays consistent rather than adding a dependency for six tests."""
    return asyncio.run(coro)


def _pushed(frame: Any) -> FramePushed:
    return FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0)


def _transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="caller", timestamp="2026-09-03T11:00:00Z")


def test_a_failed_write_never_interrupts_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule that matters most: losing a turn is bad, dropping the caller to avoid it is worse."""
    def broken() -> Any:
        raise RuntimeError("database is away")

    monkeypatch.setattr(recording, "get_sessionmaker", broken)
    recorder = ConversationRecorder(uuid.uuid4())

    _run(recorder.turn("caller", "Guten Tag"))
    _run(recorder.tool("appointment_book", {}, {"status": "ok"}))
    _run(recorder.finish())


def test_a_tool_call_is_recorded_with_what_went_in_and_what_came_back() -> None:
    """The result is the point: it separates an agent that booked from one that said it did."""
    recorder = _Recorded()

    _run(recorder.tool(
        "appointment_book", {}, {"status": "rejected", "reason": "not recorded", "run_id": "abc123"}
    ))

    kind, payload = recorder.events[0]
    assert kind == "tool_call"
    assert payload["result"]["status"] == "rejected"
    assert payload["run_id"] == "abc123"


def test_a_tool_call_without_a_governed_run_carries_no_run_reference() -> None:
    recorder = _Recorded()

    _run(recorder.tool("appointment_find_slots", {"day": "2026-09-03"}, {"slots": []}))

    assert "run_id" not in recorder.events[0][1]


def test_an_empty_turn_is_not_recorded() -> None:
    recorder = _Recorded()

    _run(recorder.turn("agent", "   "))

    assert recorder.events == []


def test_the_observer_stores_each_side_of_the_call_as_one_turn() -> None:
    """The agent's speech arrives in fragments; a timeline of clauses is unreadable."""
    recorder = _Recorded()
    observer = _TranscriptObserver(recorder)

    _run(observer.on_push_frame(_pushed(_transcription("Ich bräuchte einen Termin."))))
    for fragment in ("Gerne. ", "Wie ist Ihr Vorname?"):
        _run(observer.on_push_frame(_pushed(TTSTextFrame(text=fragment, aggregated_by="sentence"))))
    _run(observer.on_push_frame(_pushed(BotStoppedSpeakingFrame())))

    assert [payload for _, payload in recorder.events] == [
        {"role": "caller", "text": "Ich bräuchte einen Termin."},
        {"role": "agent", "text": "Gerne.  Wie ist Ihr Vorname?"},
    ]


def test_the_observer_does_not_emit_an_empty_agent_turn() -> None:
    """A caller interrupting before the agent speaks must not leave a blank line in the record."""
    recorder = _Recorded()
    observer = _TranscriptObserver(recorder)

    _run(observer.on_push_frame(_pushed(BotStoppedSpeakingFrame())))

    assert recorder.events == []


def _report(path: Path, results: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"snapshot": {}, "results": results}), encoding="utf-8")
    return path


def test_a_run_is_kept_and_latest_points_at_it(tmp_path: Path) -> None:
    """Overwriting the only report makes "did this change help?" unanswerable."""
    first = write_report([{"id": "a", "passed": True}], tmp_path)
    second = write_report([{"id": "a", "passed": False}], tmp_path)

    assert first != second
    assert len(list(tmp_path.glob("2*.json"))) == 2
    assert json.loads((tmp_path / "latest.json").read_text())["results"][0]["passed"] is False


def test_comparing_two_runs_names_what_moved_in_each_direction(tmp_path: Path) -> None:
    """A total hides a change that fixes four scenarios and breaks three."""
    before = _report(
        tmp_path / "before.json",
        [{"id": "fixed", "passed": False}, {"id": "broken", "passed": True},
         {"id": "gone", "passed": True}, {"id": "skipped-one", "skipped": "needs audio"}],
    )
    after = _report(
        tmp_path / "after.json",
        [{"id": "fixed", "passed": True}, {"id": "broken", "passed": False},
         {"id": "new", "passed": True}],
    )

    moved = compare(before, after)

    assert moved["fixed"] == ["fixed"]
    assert moved["broken"] == ["broken"]
    assert moved["only_in_after"] == ["new"]
    assert moved["only_in_before"] == ["gone"]


def test_a_skipped_scenario_is_not_counted_as_a_regression(tmp_path: Path) -> None:
    """A scenario that never ran has no verdict to have changed."""
    before = _report(tmp_path / "b.json", [{"id": "s", "passed": True}])
    after = _report(tmp_path / "a.json", [{"id": "s", "skipped": "needs audio fixtures"}])

    moved = compare(before, after)

    assert moved["broken"] == [] and moved["only_in_before"] == ["s"]


class _Event:
    """The two fields `_outcome` reads. Avoids a database for logic that needs none."""

    def __init__(self, kind: str, payload: dict[str, Any]) -> None:
        self.kind = kind
        self.payload = payload


def _turn(text: str) -> _Event:
    return _Event("turn", {"role": "agent", "text": text})


def _tool(name: str, result: dict[str, Any], run_id: str | None = None) -> _Event:
    payload: dict[str, Any] = {"tool": name, "arguments": {}, "result": result}
    if run_id:
        payload["run_id"] = run_id
    return _Event("tool_call", payload)


def test_a_call_that_attempted_nothing_consequential_reports_no_outcome() -> None:
    """"No booking attempt" and "the booking failed" are different, and must not look alike."""
    events = [_turn("Guten Tag"), _tool("appointment_find_slots", {"slots": []})]

    assert _outcome(events) is None


def test_the_outcome_is_the_engine_ruling_not_what_the_agent_said_next() -> None:
    """The whole point of the column: an agent can claim anything after a blocked write."""
    events = [
        _tool("appointment_book", {"status": "blocked", "reason": "taken"}, run_id="r-1"),
        _turn("Alles klar, der Termin ist gebucht!"),
    ]

    assert _outcome(events) == "blocked"


def test_the_last_governed_attempt_decides() -> None:
    """A caller who corrects a detail and rebooks should show the booking that stuck."""
    events = [
        _tool("appointment_book", {"status": "blocked"}, run_id="r-1"),
        _turn("Dann halb zehn."),
        _tool("appointment_book", {"status": "ok"}, run_id="r-2"),
    ]

    assert _outcome(events) == "ok"


def test_a_tool_call_with_no_governed_run_cannot_set_the_outcome() -> None:
    """Only the engine rules. A reversible tool's own status must never colour the verdict."""
    events = [
        _tool("appointment_book", {"status": "ok"}, run_id="r-1"),
        _tool("appointment_set_details", {"status": "ok", "missing": []}),
    ]

    assert _outcome(events) == "ok"
