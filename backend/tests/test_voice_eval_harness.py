"""The versioned voice suite keeps its promised breadth and executable contract."""

from app.workloads.conversational.evals.harness import REQUIRED_TAGS, load_scenarios


def test_voice_eval_suite_is_valid_and_covers_demo_risks() -> None:
    scenarios = load_scenarios()
    covered = set().union(*(scenario.tags for scenario in scenarios))

    assert 30 <= len(scenarios) <= 50
    assert REQUIRED_TAGS <= covered


def test_voice_eval_scenarios_define_outcomes_not_exact_agent_wording() -> None:
    scenarios = load_scenarios()

    assert all(scenario.expected for scenario in scenarios)
    assert all(not expectation.startswith("agent:") for scenario in scenarios for expectation in scenario.expected)
