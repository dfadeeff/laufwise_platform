"""Replay a scenario against the real agent.

The point of this module is that nothing here is a stand-in for the thing being tested. A run uses
the production instructions (`prompts/base.md`), the production tool definitions (`booking.TOOLS`)
and a real `BookingSession`, so a booking in an eval goes through the same governed contract — the
same preconditions, the same postcondition re-query — as a booking on a live call. Only the audio
layer is absent: `turns` are the transcript STT would have produced.

That absence is reported, never papered over. A scenario whose `environment` can only exist in
audio (an interruption, background noise, a quiet caller) is SKIPPED with its reason rather than
counted as a pass; a suite that reports 46 passes when 12 of them never ran is worse than no suite.

`environment` is otherwise honoured as fault injection, which is the half that catches real bugs:
a provider that times out, an availability read that comes back empty, and above all a write that
claims success and persists nothing — the case where the agent must not tell the caller "booked".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import settings
from app.connectors.base import Appointment
from app.workloads.conversational.booking import REF_PREFIX, TOOLS, BookingSession
from app.workloads.conversational.evals.harness import VoiceScenario
from app.workloads.conversational.sessions import VoiceLanguage
from app.workloads.conversational.surface import _PROMPT_PATH, _instructions

# Environment keys that only manifest in audio. The transcript is already clean text, so replaying
# it would exercise nothing these describe — the scenario is skipped rather than falsely passed.
AUDIO_ONLY = frozenset(
    {"interrupt_at_ms", "overlap", "backchannel", "internal_pause_ms",
     "noise", "snr_db", "gain_db", "stt_confidence"}
)

# A turn should not need more tool rounds than this. Hitting it is itself a finding — an agent
# looping on a tool is a failure mode, not a reason to keep paying for tokens.
MAX_TOOL_ROUNDS = 6


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class ScenarioRun:
    """What actually happened — the record a judge rules on and a human can read."""

    scenario_id: str
    skipped: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    appointments: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def booked(self) -> bool:
        return any(
            call.name == "appointment_book" and call.result.get("status") == "ok"
            for call in self.tool_calls
        )

    def invariants(self) -> list[str]:
        """Failures no judge should be asked to rule on, because they are decidable.

        These are the fail-closed half: an appointment that exists without a confirmed booking
        means the governed loop leaked, and more than one means the agent double-booked a caller.
        """
        broken = []
        if self.appointments and not self.booked:
            broken.append("an appointment exists without a booking that returned ok")
        if len(self.appointments) > 1:
            broken.append(f"{len(self.appointments)} appointments created for one caller")
        return broken


def snapshot() -> dict[str, str]:
    """What a result refers to. A pass means nothing without the version it passed against."""
    return {
        "prompt_sha": sha256(_PROMPT_PATH.read_bytes()).hexdigest()[:12],
        "contract": "voice_appointment@1",
        "tools": ",".join(spec.name for spec in TOOLS),
        "agent_model": settings.voice_llm_model,
    }


def language_for(scenario: VoiceScenario) -> VoiceLanguage:
    """The session language a scenario means to test.

    Declared in `environment` where it matters, otherwise read off the id — running an English
    scenario in a German session would test the agent's no-switching rule instead of the
    behaviour the scenario was written for.
    """
    declared = scenario.environment.get("language")
    if declared in ("de", "en", "ar"):
        return declared  # type: ignore[return-value]
    if scenario.scenario_id.startswith(("english", "en-")):
        return "en"
    if scenario.scenario_id.startswith("arabic"):
        return "ar"
    return "de"


def _inject(session: BookingSession, environment: dict[str, Any]) -> BookingSession:
    """Make the named tool misbehave the way the scenario describes.

    Faults are injected BELOW the tool, at the calendar, so the tool and the governed step are the
    real ones. `write_acknowledged` is the important one: the write is acknowledged and nothing is
    persisted, so the postcondition — not the tool's word — decides, and the agent must not claim
    success.
    """
    target, result, error = (
        environment.get("tool"),
        environment.get("result") or environment.get("first_result"),
        environment.get("error"),
    )
    if target is None:
        return session

    if target == "appointment_find_slots":
        if error is not None:
            def unavailable(day: str) -> dict[str, Any]:
                return {"day": day, "slots": [], "reason": f"the calendar is unreachable ({error})"}

            session.find_slots = unavailable  # type: ignore[method-assign]
        elif result == "empty":
            original = session.find_slots

            def empty(day: str) -> dict[str, Any]:
                return {**original(day), "slots": [], "reason": "that day is fully booked"}

            session.find_slots = empty  # type: ignore[method-assign]

    if target in ("appointment_book", "book_appointment"):
        if error is not None:
            def failed() -> dict[str, Any]:
                return {"status": "blocked", "reason": f"the calendar is unreachable ({error})",
                        "missing": session.missing, "appointment": None, "run_id": ""}

            session.book = failed  # type: ignore[method-assign]
        elif result == "write_acknowledged" and environment.get("postcondition") is False:
            # The tool claims the write landed; the calendar never records it. The engine's
            # postcondition re-query is the only thing standing between this and a false promise.
            session.calendar.create_appointment = lambda *a, **k: None  # type: ignore[method-assign]
        elif result == "slot_taken":
            # Someone else takes the time between the caller choosing it and the booking running.
            # Seating a real appointment (rather than faking the tool's answer) means the genuine
            # precondition is what refuses it, which is the behaviour under test.
            original_book = session.book

            def taken() -> dict[str, Any]:
                wanted = session.draft["preferred_time"]
                if wanted:
                    session.calendar.create_appointment(
                        Appointment(ref=f"taken-{wanted}", start=wanted), patient_id=0
                    )
                return original_book()

            session.book = taken  # type: ignore[method-assign]
    return session


def _openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": spec.properties,
                    "required": list(spec.required),
                },
            },
        }
        for spec in TOOLS
    ]


def run_scenario(scenario: VoiceScenario, client: Any, *, model: str | None = None) -> ScenarioRun:
    """Play the scenario's turns at the agent and record everything it did."""
    blocking = sorted(AUDIO_ONLY & set(scenario.environment))
    if blocking:
        return ScenarioRun(scenario.scenario_id, skipped=f"needs audio fixtures ({', '.join(blocking)})")

    run = ScenarioRun(scenario.scenario_id)
    session = _inject(BookingSession(scenario.scenario_id), scenario.environment)
    by_name = {spec.name: spec for spec in TOOLS}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _instructions(language_for(scenario))}
    ]

    try:
        for turn in scenario.turns:
            messages.append({"role": "user", "content": turn})
            run.transcript.append({"role": "caller", "text": turn})
            for _ in range(MAX_TOOL_ROUNDS):
                reply = client.chat.completions.create(
                    model=model or settings.voice_llm_model,
                    messages=messages,
                    tools=_openai_tools(),
                    temperature=0.2,
                ).choices[0].message
                messages.append(
                    {
                        "role": "assistant",
                        "content": reply.content,
                        **(
                            {"tool_calls": [
                                {"id": call.id, "type": "function",
                                 "function": {"name": call.function.name,
                                              "arguments": call.function.arguments}}
                                for call in reply.tool_calls
                            ]}
                            if reply.tool_calls
                            else {}
                        ),
                    }
                )
                if reply.content:
                    run.transcript.append({"role": "agent", "text": reply.content})
                if not reply.tool_calls:
                    break
                for call in reply.tool_calls:
                    arguments = json.loads(call.function.arguments or "{}")
                    spec = by_name.get(call.function.name)
                    result = (
                        spec.call(session, arguments)
                        if spec
                        else {"error": f"no such tool: {call.function.name}"}
                    )
                    run.tool_calls.append(ToolCall(call.function.name, arguments, result))
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
                    )
            else:
                run.error = f"agent did not finish a turn within {MAX_TOOL_ROUNDS} tool rounds"
    except Exception as exc:  # noqa: BLE001 — one scenario must not take the suite down
        run.error = f"{type(exc).__name__}: {exc}"

    # Only the agent's own bookings. Fault injection seats appointments to occupy a slot; counting
    # those would report the scenario's setup as something the agent did.
    run.appointments = [
        appt.start for appt in session.calendar.appointments if appt.ref.startswith(REF_PREFIX)
    ]
    return run


def write_report(runs: list[dict[str, Any]], directory: Path) -> str:
    """Keep every run, and point `latest.json` at the newest.

    Runs are kept rather than overwritten because the useful question is never "did it pass?" but
    "did this change help?", and that needs two runs to compare. The filename carries the prompt
    hash, so a report says which agent it describes without being opened.
    """
    directory.mkdir(parents=True, exist_ok=True)
    identity = snapshot()
    stem = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{identity['prompt_sha']}"
    # Two runs of the same prompt inside one second must not silently become one report.
    stamped = directory / f"{stem}.json"
    attempt = 1
    while stamped.exists():
        stamped = directory / f"{stem}-{attempt}.json"
        attempt += 1
    body = json.dumps({"snapshot": identity, "results": runs}, indent=2, ensure_ascii=False)
    stamped.write_text(body, encoding="utf-8")
    (directory / "latest.json").write_text(body, encoding="utf-8")
    return str(stamped)


def compare(before: Path, after: Path) -> dict[str, list[str]]:
    """Which scenarios changed verdict between two runs.

    A total is too coarse to act on — a prompt edit that fixes four scenarios and breaks three
    barely moves it. What matters is which ones moved, and in which direction.
    """
    def verdicts(path: Path) -> dict[str, bool]:
        report = json.loads(path.read_text(encoding="utf-8"))
        return {
            result["id"]: bool(result.get("passed"))
            for result in report["results"]
            if "skipped" not in result
        }

    old, new = verdicts(before), verdicts(after)
    shared = old.keys() & new.keys()
    return {
        "fixed": sorted(name for name in shared if not old[name] and new[name]),
        "broken": sorted(name for name in shared if old[name] and not new[name]),
        "only_in_after": sorted(new.keys() - old.keys()),
        "only_in_before": sorted(old.keys() - new.keys()),
    }
