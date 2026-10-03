"""The versioned voice suite keeps its promised breadth and executable contract."""

from app.workloads.conversational.evals.harness import (
    MAX_SCENARIOS,
    MIN_SCENARIOS,
    REQUIRED_TAGS,
    load_scenarios,
)


def test_voice_eval_suite_is_valid_and_covers_demo_risks() -> None:
    scenarios = load_scenarios()
    covered = set().union(*(scenario.tags for scenario in scenarios))

    assert MIN_SCENARIOS <= len(scenarios) <= MAX_SCENARIOS
    assert REQUIRED_TAGS <= covered


def test_voice_eval_scenarios_define_outcomes_not_exact_agent_wording() -> None:
    scenarios = load_scenarios()

    assert all(scenario.expected for scenario in scenarios)
    assert all(not expectation.startswith("agent:") for scenario in scenarios for expectation in scenario.expected)


def test_the_nightly_smoke_set_is_small_and_never_needs_audio() -> None:
    """CI replays `smoke` every night against the real model. A scenario it can only skip would
    be paid for and prove nothing, and a large set turns a nightly check into a bill."""
    from app.workloads.conversational.evals.runner import AUDIO_ONLY

    smoke = [s for s in load_scenarios() if "smoke" in s.tags]

    assert 6 <= len(smoke) <= 12
    assert not [s.scenario_id for s in smoke if AUDIO_ONLY & set(s.environment)]
