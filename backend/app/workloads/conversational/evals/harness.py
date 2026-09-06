"""Load, validate and replay the voice scenario suite.

Validating the file is free and is what `--tag`/no arguments do. `--run` replays scenarios against
the real agent, which costs model calls, so it is never the default and never happens inside the
ordinary test run.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUITE_PATH = Path(__file__).with_name("scenarios.json")
REQUIRED_TAGS = {
    "happy_path",
    "interruption",
    "hesitation",
    "noise",
    "quiet_audio",
    "accent",
    "domain_vocabulary",
    "numbers",
    "latency",
    "no_availability",
    "provider_failure",
    "multilingual",
    # The dimensions the practice specification's acceptance tests add (§8). Each one names a way
    # the agent can fail that is not a booking bug: changing an appointment it should not have,
    # disclosing something it could not verify, saying the wrong thing about money, giving medical
    # advice, or quoting a price nobody published. A suite that dropped these would still pass
    # while the agent did real harm.
    "lifecycle",
    "privacy",
    "money",
    "safety",
    "knowledge",
}

# The suite's size bounds. The floor stops it thinning out into a smoke test; the ceiling is what
# one review pass and one replay budget can actually carry. Raised from 50 when the practice
# specification's 22 acceptance tests were added (§8) — they are requirements, not extras.
MIN_SCENARIOS, MAX_SCENARIOS = 30, 100


@dataclass(frozen=True)
class VoiceScenario:
    scenario_id: str
    turns: tuple[str, ...]
    tags: frozenset[str]
    expected: tuple[str, ...]
    environment: dict[str, object]
    audio_file: str | None = None


def load_scenarios(path: Path = SUITE_PATH) -> list[VoiceScenario]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenarios = [
        VoiceScenario(
            scenario_id=item["id"],
            turns=tuple(item["turns"]),
            tags=frozenset(item["tags"]),
            expected=tuple(item["expected"]),
            environment=item.get("environment", {}),
            audio_file=item.get("audio_file"),
        )
        for item in raw["scenarios"]
    ]
    validate_suite(scenarios, path.parent)
    return scenarios


def validate_suite(scenarios: list[VoiceScenario], fixture_dir: Path) -> None:
    if not MIN_SCENARIOS <= len(scenarios) <= MAX_SCENARIOS:
        raise ValueError(
            f"voice suite must contain {MIN_SCENARIOS}-{MAX_SCENARIOS} scenarios"
        )
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("voice scenario ids must be unique")
    for scenario in scenarios:
        if not scenario.turns or not all(turn.strip() for turn in scenario.turns):
            raise ValueError(f"{scenario.scenario_id}: at least one non-empty turn is required")
        if not scenario.expected:
            raise ValueError(f"{scenario.scenario_id}: expected behavior is required")
        if scenario.audio_file and not (fixture_dir / scenario.audio_file).is_file():
            raise ValueError(f"{scenario.scenario_id}: missing audio fixture {scenario.audio_file}")
    covered = set().union(*(scenario.tags for scenario in scenarios))
    if missing := REQUIRED_TAGS - covered:
        raise ValueError(f"voice suite is missing required coverage: {sorted(missing)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate, inspect and replay Laufwise voice evals")
    parser.add_argument("--tag", help="only scenarios with this tag")
    parser.add_argument("--id", help="only this scenario")
    parser.add_argument("--limit", type=int, help="stop after N scenarios (keeps a check cheap)")
    parser.add_argument(
        "--run",
        action="store_true",
        help="replay against the real agent and judge the results (costs model calls)",
    )
    parser.add_argument("--reports", default="runs/evals", help="directory to keep run reports in")
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        metavar="N",
        help="attempts per scenario; a scenario passes on a MAJORITY (default: 3). One attempt "
        "against a non-deterministic model judged by another model is a coin toss, not a score — "
        "single-run totals on this suite swing by three scenarios with no code change at all.",
    )
    parser.add_argument(
        "--model",
        help="agent model to replay against (default: VOICE_LLM_MODEL). A residual failure is "
        "either a prompt the agent cannot follow or a model that cannot follow it; this is how "
        "you tell the two apart before spending another day on the prompt.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="show which scenarios changed verdict between two reports, then exit",
    )
    args = parser.parse_args()

    if args.compare:
        from app.workloads.conversational.evals.runner import compare

        for label, names in compare(Path(args.compare[0]), Path(args.compare[1])).items():
            print(f"{label}: {', '.join(names) if names else 'none'}")
        return

    scenarios = load_scenarios()
    selected = [
        scenario
        for scenario in scenarios
        if (not args.tag or args.tag in scenario.tags)
        and (not args.id or args.id == scenario.scenario_id)
    ][: args.limit]

    if not args.run:
        for scenario in selected:
            print(f"{scenario.scenario_id}: {', '.join(sorted(scenario.tags))}")
        print(f"validated {len(scenarios)} scenarios; selected {len(selected)}")
        return

    # Imported here so validating the suite never needs an API key or the OpenAI SDK.
    from openai import OpenAI

    from app.config import settings
    from app.workloads.conversational.evals.runner import run_scenario, snapshot, write_report

    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set — --run needs it to reach the agent")

    client = OpenAI(api_key=settings.openai_api_key)
    print(f"snapshot: {dict(snapshot(), agent_model=args.model or settings.voice_llm_model)}")
    results, failed, skipped = [], 0, 0

    for scenario in selected:
        run = run_scenario(scenario, client, model=args.model)
        if run.skipped:
            skipped += 1
            print(f"SKIP {scenario.scenario_id}: {run.skipped}")
            results.append({"id": scenario.scenario_id, "skipped": run.skipped})
            continue
        attempts = [_score(scenario, run, client)]
        for _ in range(max(1, args.repeat) - 1):
            attempts.append(
                _score(scenario, run_scenario(scenario, client, model=args.model), client)
            )

        wins = sum(1 for attempt in attempts if attempt["passed"])
        ok = wins * 2 > len(attempts)
        failed += not ok
        tally = f"{wins}/{len(attempts)}" if len(attempts) > 1 else ""
        flaky = " FLAKY" if 0 < wins < len(attempts) else ""
        print(f"{'PASS' if ok else 'FAIL'} {scenario.scenario_id} {tally}{flaky}")
        # Report a losing attempt: on a failure that is what went wrong, and on a flaky pass it is
        # the half that would have been invisible under single-run scoring.
        worst = next((a for a in attempts if not a["passed"]), attempts[0])
        if not ok or flaky:
            for reason in worst["invariants_broken"]:
                print(f"     invariant: {reason}")
            if worst["error"]:
                print(f"     error: {worst['error']}")
            for verdict in worst["verdicts"]:
                if not verdict["passed"]:
                    print(f"     expected {verdict['expectation']!r}: {verdict['reason']}")
        results.append(
            {
                "id": scenario.scenario_id,
                "passed": ok,
                "attempts": len(attempts),
                "wins": wins,
                # Kept so a later `--compare` can tell a scenario that genuinely regressed from one
                # that was always a coin toss — the distinction single-run reports cannot make.
                "flaky": bool(flaky),
                **{k: worst[k] for k in ("invariants_broken", "error", "transcript",
                                          "tool_calls", "appointments", "verdicts")},
            }
        )

    report = write_report(results, Path(args.reports))
    ran = len(selected) - skipped
    unstable = [r["id"] for r in results if r.get("flaky")]
    print(f"\nran {ran}, passed {ran - failed}, failed {failed}, skipped {skipped}")
    if unstable:
        # Named rather than buried: a scenario that passes two times in three is not evidence the
        # agent works, and it is the reason a total moves on its own.
        print(f"unstable ({len(unstable)}): {', '.join(unstable)}")
    print(f"report: {report}")
    raise SystemExit(1 if failed else 0)


def _score(scenario: Any, run: Any, client: Any) -> dict[str, Any]:
    """One attempt, reduced to a verdict plus the evidence behind it."""
    from app.workloads.conversational.evals.judge import judge

    broken = run.invariants()
    verdicts = [] if run.error else judge(scenario, run, client)
    return {
        "passed": not broken and not run.error and all(v.passed for v in verdicts),
        "invariants_broken": broken,
        "error": run.error,
        "transcript": run.transcript,
        "tool_calls": [
            {"tool": c.name, "arguments": c.arguments, "returned": c.result}
            for c in run.tool_calls
        ],
        "appointments": run.appointments,
        "verdicts": [
            {"expectation": v.expectation, "passed": v.passed, "reason": v.reason}
            for v in verdicts
        ],
    }


if __name__ == "__main__":
    main()
