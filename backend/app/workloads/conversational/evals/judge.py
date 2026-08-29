"""Rule on whether a replayed run did what the scenario expected.

A conversational agent cannot be checked by string equality — "Thursday at half past nine" and
"9:30 on Thursday" are the same answer. So the expectations in `scenarios.json` are written in
plain language and a model rules on them.

The judge is given the TOOL RECORD as well as the transcript, and told the record is the ground
truth. That is the difference between judging an agent and judging its prose: an agent that says
"you're booked" while `appointment_book` returned `blocked` has failed, however fluent it sounded,
and the judge can see that rather than having to infer it.

Decidable failures never reach the judge — `ScenarioRun.invariants()` settles those first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.workloads.conversational.evals.harness import VoiceScenario
from app.workloads.conversational.evals.runner import ScenarioRun

JUDGE_MODEL = "gpt-4.1"

_INSTRUCTIONS = """You score one replayed call from an appointment-booking voice agent.

You are given the transcript and the tool record. The TOOL RECORD IS THE TRUTH: it says what the
agent actually did and what the calendar actually returned. The transcript only says what the
agent claimed.

For each expectation, decide `pass` or `fail`:
- Judge the behaviour described, never the wording. Different phrasing for the same outcome passes.
- An agent that told the caller an appointment is booked when no `appointment_book` call returned
  status "ok" always fails, no matter how the expectation is worded.
- An agent that offered a time no `appointment_find_slots` call returned has invented availability
  and fails.
- Do not reward or punish tone, length, or politeness unless the expectation is about it.
- If the transcript does not contain enough to decide, fail it and say what was missing.

Reply as JSON: {"verdicts": [{"expectation": "...", "verdict": "pass"|"fail", "reason": "..."}]}
Include one entry per expectation, in the order given."""


@dataclass
class Verdict:
    expectation: str
    passed: bool
    reason: str


def judge(scenario: VoiceScenario, run: ScenarioRun, client: Any, *, model: str = JUDGE_MODEL) -> list[Verdict]:
    evidence = {
        "expectations": list(scenario.expected),
        "transcript": run.transcript,
        "tool_record": [
            {"tool": call.name, "arguments": call.arguments, "returned": call.result}
            for call in run.tool_calls
        ],
        "appointments_in_calendar": run.appointments,
    }
    reply = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    ).choices[0].message.content
    verdicts = json.loads(reply or "{}").get("verdicts", [])
    return [
        Verdict(
            expectation=str(item.get("expectation", "")),
            passed=str(item.get("verdict", "")).lower() == "pass",
            reason=str(item.get("reason", "")),
        )
        for item in verdicts
    ]
