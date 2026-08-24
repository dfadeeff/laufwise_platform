"""Load and validate replayable voice scenarios without calling production providers."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


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
}


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
    if not 30 <= len(scenarios) <= 50:
        raise ValueError("voice suite must contain 30-50 scenarios")
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
    parser = argparse.ArgumentParser(description="Validate and inspect Laufwise voice evals")
    parser.add_argument("--tag", help="show only scenarios with this tag")
    args = parser.parse_args()
    scenarios = load_scenarios()
    selected = [scenario for scenario in scenarios if not args.tag or args.tag in scenario.tags]
    for scenario in selected:
        print(f"{scenario.scenario_id}: {', '.join(sorted(scenario.tags))}")
    print(f"validated {len(scenarios)} scenarios; selected {len(selected)}")


if __name__ == "__main__":
    main()
