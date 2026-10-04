"""Shadow calls — comparing two voice engines on real traffic, because nothing else can.

The eval suite replays text: the prompt, the tools and a real booking session. It proves what the
agent DOES, and it is the same proof for both engines because both are handed the same prompt and
the same tools. What it cannot see is the half that made speech-to-speech worth building — how
quickly the agent answers, whether it talks over the caller, whether a birth date read out digit
by digit survives the turn detector. Those only exist in audio, and audio only exists on a call.

So the honest way to certify realtime is to place calls on both engines and compare what the
calls left behind. This reads that evidence back:

    python -m app.workloads.conversational.shadow --days 7
    python -m app.workloads.conversational.shadow --days 7 --tenant <uuid> --json

It computes nothing the database does not already contain. Every number below comes from rows the
platform writes during an ordinary call — the conversation, its turns, its tool calls and their
durations — so a comparison can be run after the fact, on calls nobody set up as an experiment.

What it deliberately does NOT do is decide. There is no pass mark here: a median turn count that
drops by one is not automatically an improvement, and it is the person reading it who knows
whether the calls felt better.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import Conversation
from app.db.session import get_sessionmaker

# Ordered so the table reads cascaded-then-realtime whatever the data contains.
ENGINES = ("cascaded", "realtime")


@dataclass
class Cohort:
    """Every call one engine handled, and what it left behind."""

    engine: str
    calls: int = 0
    seconds: list[float] = field(default_factory=list)
    caller_turns: list[int] = field(default_factory=list)
    agent_turns: list[int] = field(default_factory=list)
    tool_ms: list[int] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)
    unfinished: int = 0

    def add(self, conversation: Conversation) -> None:
        self.calls += 1
        if conversation.ended_at and conversation.started_at:
            self.seconds.append((conversation.ended_at - conversation.started_at).total_seconds())
        else:
            # A call with no end is a call the pipeline never closed out — a crash, a dropped
            # socket, a process restart. Counted rather than filtered: it is the failure mode a
            # new engine is most likely to introduce.
            self.unfinished += 1
        caller = agent = 0
        for event in conversation.events:
            if event.kind == "turn":
                if event.payload.get("role") == "caller":
                    caller += 1
                else:
                    agent += 1
            elif event.kind == "tool_call":
                if (elapsed := event.payload.get("duration_ms")) is not None:
                    self.tool_ms.append(int(elapsed))
            elif event.kind == "call_summary":
                outcome = str(event.payload.get("summary", {}).get("outcome", "unknown"))
                self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1
        self.caller_turns.append(caller)
        self.agent_turns.append(agent)

    def report(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "calls": self.calls,
            "unfinished": self.unfinished,
            "median_seconds": _median(self.seconds),
            "median_caller_turns": _median(self.caller_turns),
            "median_agent_turns": _median(self.agent_turns),
            "tool_calls": len(self.tool_ms),
            "tool_ms_median": _median(self.tool_ms),
            "tool_ms_p95": _percentile(self.tool_ms, 95),
            "outcomes": dict(sorted(self.outcomes.items())),
        }


def _median(values: list[float] | list[int]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _percentile(values: list[int], pct: int) -> int | None:
    """The slow end, which is what a caller remembers. Nearest-rank; exact enough for twenty calls
    and honest about it — a p95 over a handful of samples is a sentence, not a statistic."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


async def compare(*, days: int, tenant_id: uuid.UUID | None = None) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_sessionmaker()() as session:
        stmt = select(Conversation).where(Conversation.started_at >= since)
        if tenant_id is not None:
            stmt = stmt.where(Conversation.tenant_id == tenant_id)
        conversations = (await session.execute(stmt)).scalars().unique().all()
        cohorts = {engine: Cohort(engine) for engine in ENGINES}
        skipped = 0
        for conversation in conversations:
            # Calls placed before the engine was recorded cannot be attributed to one, and
            # guessing would be the one thing that makes the comparison worthless.
            engine = (conversation.metadata_ or {}).get("engine")
            if engine in cohorts:
                await session.refresh(conversation, ["events"])
                cohorts[engine].add(conversation)
            else:
                skipped += 1
    return {
        "since": since.isoformat(),
        "unattributed_calls": skipped,
        "cohorts": [cohorts[engine].report() for engine in ENGINES],
    }


def _print(result: dict[str, Any]) -> None:
    print(f"calls since {result['since'][:16]}")
    if result["unattributed_calls"]:
        print(f"  ({result['unattributed_calls']} older calls carry no engine and are excluded)")
    header = f"{'':<10}{'calls':>7}{'unfin':>7}{'median s':>10}{'caller':>8}{'agent':>7}{'tool ms':>9}{'p95':>7}"
    print(header)
    for cohort in result["cohorts"]:
        print(
            f"{cohort['engine']:<10}{cohort['calls']:>7}{cohort['unfinished']:>7}"
            f"{_cell(cohort['median_seconds']):>10}{_cell(cohort['median_caller_turns']):>8}"
            f"{_cell(cohort['median_agent_turns']):>7}{_cell(cohort['tool_ms_median']):>9}"
            f"{_cell(cohort['tool_ms_p95']):>7}"
        )
    for cohort in result["cohorts"]:
        if cohort["outcomes"]:
            outcomes = " · ".join(f"{k} {v}" for k, v in cohort["outcomes"].items())
            print(f"  {cohort['engine']}: {outcomes}")
    thin = [c["engine"] for c in result["cohorts"] if 0 < c["calls"] < 10]
    if thin:
        print(f"  note: {', '.join(thin)} has under ten calls — read this as an anecdote, not a result")


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=7, help="how far back to look")
    parser.add_argument("--tenant", help="one practice only, by tenant id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    result = asyncio.run(
        compare(days=args.days, tenant_id=uuid.UUID(args.tenant) if args.tenant else None)
    )
    print(json.dumps(result, indent=2)) if args.json else _print(result)


if __name__ == "__main__":
    main()
