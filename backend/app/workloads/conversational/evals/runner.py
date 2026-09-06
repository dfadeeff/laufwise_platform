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
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.config import settings
from app.connectors.base import Appointment
from app.workloads.conversational.booking import REF_PREFIX, TOOLS, BookingSession
from app.workloads.conversational.evals.harness import VoiceScenario
from app.workloads.conversational.sessions import VoiceLanguage
from app.workloads.conversational.skills import allowed_tools, load_skills
from app.workloads.conversational.surface import (
    GREETING_INSTRUCTION,
    _PROMPT_PATH,
    _instructions,
)

# Environment keys that only manifest in audio. The transcript is already clean text, so replaying
# it would exercise nothing these describe — the scenario is skipped rather than falsely passed.
AUDIO_ONLY = frozenset(
    {"interrupt_at_ms", "overlap", "backchannel", "internal_pause_ms",
     "noise", "snr_db", "gain_db", "stt_confidence"}
)

# A turn should not need more tool rounds than this. Hitting it is itself a finding — an agent
# looping on a tool is a failure mode, not a reason to keep paying for tokens.
MAX_TOOL_ROUNDS = 6


# Tools that take something the caller said. A cancellation carries the name and birth date to
# `get_patient_appointments` as arguments and never touches the draft, so checking only for
# `appointment_set_details` failed a call that had verified the caller correctly and said both of
# the practice's approved sentences — the invariant has to know where details legitimately go.
_CONSUMES_CALLER_DATA = frozenset(
    {
        "appointment_set_details",
        "get_patient_appointments",
        "create_callback_request",
        "find_patient",
    }
)


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

        The third is the one the evals kept catching by accident. An agent that says "I've noted
        your date of birth" and calls no tool has told the caller their data is safe when nothing
        was stored — and it is the same failure whether the value was right or invented. Checking
        it here makes it a hard failure instead of something a judge might or might not notice.
        """
        broken = []
        if self.appointments and not self.booked:
            broken.append("an appointment exists without a booking that returned ok")
        if len(self.appointments) > 1:
            broken.append(f"{len(self.appointments)} appointments created for one caller")
        if self._claims_to_have_recorded() and not any(
            call.name in _CONSUMES_CALLER_DATA for call in self.tool_calls
        ):
            broken.append("told the caller a detail was recorded without ever recording one")
        return broken

    def _claims_to_have_recorded(self) -> bool:
        """Whether any agent turn asserts a detail was taken down.

        Deliberately conservative — it only fires when the caller's details reached NO tool at all
        in the entire call, so a mis-ordered but honest conversation never trips it.
        """
        said = " ".join(t["text"].casefold() for t in self.transcript if t["role"] == "agent")
        return any(
            claim in said
            for claim in (
                "notiert", "aufgenommen", "korrigiert", "gespeichert", "vermerkt",
                "i have recorded", "i've recorded", "i have noted", "i've noted",
                "записал", "записала", "отметил",
            )
        )


def snapshot() -> dict[str, str]:
    """What a result refers to. A pass means nothing without the version it passed against."""
    return {
        "prompt_sha": sha256(_PROMPT_PATH.read_bytes()).hexdigest()[:12],
        "contract": "voice_appointment@2",
        "skills": ",".join(f"{s.name}@{len(s.tools)}" for s in load_skills()),
        "tools": ",".join(spec.name for spec in TOOLS if spec.name in allowed_tools()),
        "agent_model": settings.voice_llm_model,
    }


def language_for(scenario: VoiceScenario) -> VoiceLanguage:
    """The session language a scenario means to test.

    Declared in `environment` where it matters, otherwise read off the id — running an English
    scenario in a German session would test the agent's no-switching rule instead of the
    behaviour the scenario was written for.
    """
    declared = scenario.environment.get("language")
    if declared in ("de", "en", "ru", "ar"):
        return declared  # type: ignore[return-value]
    if scenario.scenario_id.startswith(("english", "en-")):
        return "en"
    if scenario.scenario_id.startswith(("russian", "ru-")):
        return "ru"
    if scenario.scenario_id.startswith("arabic"):
        return "ar"
    return "de"


# The patient every "existing appointment" scenario is written against. One fixed identity, so a
# scenario's turns can quote a name, a birth date and an appointment time that really match — which
# is the only way to exercise the verification gate rather than a rejection.
EXISTING_PATIENT = {
    "vorname": "Anna",
    "nachname": "Weber",
    "geburtsdatum": "1971-04-12",
    "telefon": "+4917642899911",
}


def _seed_existing(session: BookingSession, spec: Any) -> None:
    """Give the session a patient card and one or more appointments already in the book.

    Rescheduling, cancelling and verification are all about an appointment that EXISTS, and a
    scenario cannot create one first — the agent would have to book it, which is a different test.
    Seeded through the real calendar so the agent reaches it through the real lookup.

    `spec` is a list of `YYYY-MM-DDTHH:MM` starts, `"short_notice"` for one a few hours from now,
    or `true` for one at the next open 09:00.
    """
    from app.connectors.base import Patient

    if spec == "short_notice":
        starts = [_short_notice_slot()]
    elif isinstance(spec, list):
        starts = spec
    else:
        starts = [_next_open_slot()]
    card = session.calendar.create_patient(Patient(**EXISTING_PATIENT))
    for index, start in enumerate(starts):
        session.calendar.create_appointment(
            Appointment(
                ref=f"existing-{index}",
                start=str(start),
                type="Medizinische Fußpflege",
                raw={"resource": "MA1"},
            ),
            patient_id=card.id,
        )


def _short_notice_slot(now: datetime | None = None) -> str:
    """A real grid slot inside the 24-hour notice window, so the warning genuinely applies.

    Every constraint here comes from a way the earlier version broke. It has to be ON the grid,
    because `now + 3h` after three in the afternoon is outside opening hours and the practice
    would never have booked it. It has to be at least an hour out, so a scenario is not racing
    the clock while it runs. And it has to be under 24 hours, or the Ausfallhonorar sentence the
    scenario exists to test does not apply at all.
    """
    from app.workloads.conversational.practice import load_practice

    now = now or datetime.now()
    schedule = load_practice().schedule
    earliest, latest = now + timedelta(hours=1), now + timedelta(hours=24)
    for offset in (0, 1):
        day = (now + timedelta(days=offset)).date()
        for start in schedule.starts_on(day):
            if earliest <= start <= latest:
                return start.strftime("%Y-%m-%dT%H:%M")
    # Only reachable across a closed weekend, where no short-notice slot can exist. Fall back to
    # the next open slot; the scenario then tests the ordinary path, which is honest.
    return _next_open_slot()


def _next_open_slot() -> str:
    """A 09:00 start a week out on an open day — far enough that today's clock cannot expire it."""
    from datetime import date, timedelta

    from app.workloads.conversational.practice import load_practice

    schedule = load_practice().schedule
    day = date.today() + timedelta(days=7)
    while not schedule.is_open(day):
        day += timedelta(days=1)
    return f"{day.isoformat()}T09:00"


def _inject(session: BookingSession, environment: dict[str, Any]) -> BookingSession:
    """Make the named tool misbehave the way the scenario describes.

    Faults are injected BELOW the tool, at the calendar, so the tool and the governed step are the
    real ones. `write_acknowledged` is the important one: the write is acknowledged and nothing is
    persisted, so the postcondition — not the tool's word — decides, and the agent must not claim
    success.
    """
    if existing := environment.get("existing_appointment"):
        _seed_existing(session, existing)

    target, result, error = (
        environment.get("tool"),
        environment.get("result") or environment.get("first_result"),
        environment.get("error"),
    )
    if target is None:
        return session

    if target in ("search_availability", "appointment_find_slots"):
        if error is not None:
            def unavailable(**kwargs: Any) -> dict[str, Any]:
                return {"slots": [], "reason": f"the calendar is unreachable ({error})"}

            session.search_availability = unavailable  # type: ignore[method-assign]
        elif result == "empty":
            def empty(**kwargs: Any) -> dict[str, Any]:
                return {"slots": [], "reason": "nothing is free in that range"}

            session.search_availability = empty  # type: ignore[method-assign]

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
                # Every calendar, not one: the three are equivalent and the booking takes whichever
                # is free, so occupying only MA1 would leave the slot bookable on MA2.
                for resource in session.calendar.schedule.resources:
                    if wanted:
                        session.calendar.create_appointment(
                            Appointment(
                                ref=f"taken-{resource}-{wanted}",
                                start=wanted,
                                raw={"resource": resource},
                            ),
                            patient_id=0,
                        )
                return original_book()

            session.book = taken  # type: ignore[method-assign]
    return session


# What a caller can say about an appointment they already have. Written into the scenario as a
# placeholder rather than a literal, because the seed moves: an appointment seeded "three hours
# from now" is a different clock time on every run, and a scenario that hardcoded one was testing
# whether the agent could guess the fixture rather than whether it could cancel.
_PLACEHOLDERS = {
    "{{appointment_day}}": lambda a: _spoken_day(a.start[:10]),
    "{{appointment_date}}": lambda a: a.start[:10],
    "{{appointment_time}}": lambda a: a.start[11:],
}

_WEEKDAY_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _spoken_day(day: str) -> str:
    """`am Dienstag, den 08.09.` — how a caller says the day of their own appointment."""
    from datetime import date as _date

    when = _date.fromisoformat(day)
    return f"am {_WEEKDAY_DE[when.weekday()]}, den {when:%d.%m.}"


def _resolve_turns(turns: tuple[str, ...], session: BookingSession) -> list[str]:
    """Fill a scenario's placeholders from the appointment actually seeded into this session."""
    existing = next(iter(session.calendar.appointments), None)
    if existing is None:
        return list(turns)
    resolved = []
    for turn in turns:
        for token, render in _PLACEHOLDERS.items():
            if token in turn:
                turn = turn.replace(token, render(existing))
        resolved.append(turn)
    return resolved


def _greet(
    run: ScenarioRun, messages: list[dict[str, Any]], client: Any, model: str | None, language: str
) -> None:
    """Play the agent's opening greeting, exactly as `on_client_connected` does on a live call."""
    messages.append({"role": "developer", "content": GREETING_INSTRUCTION[language]})
    greeting = client.chat.completions.create(
        model=model or settings.voice_llm_model,
        messages=messages,
        tools=_openai_tools(),
        temperature=0.2,
    ).choices[0].message
    messages.append({"role": "assistant", "content": greeting.content})
    if greeting.content:
        run.transcript.append({"role": "agent", "text": greeting.content})


def _openai_tools() -> list[dict[str, Any]]:
    """The tools a live caller reaches, in the same order and through the same skill allowlist."""
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
        if spec.name in allowed_tools()
    ]


def run_scenario(scenario: VoiceScenario, client: Any, *, model: str | None = None) -> ScenarioRun:
    """Play the scenario's turns at the agent and record everything it did."""
    blocking = sorted(AUDIO_ONLY & set(scenario.environment))
    if blocking:
        return ScenarioRun(scenario.scenario_id, skipped=f"needs audio fixtures ({', '.join(blocking)})")

    run = ScenarioRun(scenario.scenario_id)
    session = _inject(BookingSession(scenario.scenario_id), scenario.environment)
    turns = _resolve_turns(scenario.turns, session)
    by_name = {spec.name: spec for spec in TOOLS}
    language = language_for(scenario)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _instructions(language)}
    ]

    try:
        # The agent speaks first on a real call, so the replay does too. Without it every scenario
        # spends its opening turn on a greeting the caller has already heard, and a one-turn
        # scenario never reaches the behaviour it was written to test.
        _greet(run, messages, client, model, language)
        for turn in turns:
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
