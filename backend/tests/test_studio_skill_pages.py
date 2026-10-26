"""A published agent reads the pages of the skills it has (found while checking #130/#131).

`studio.md` told Mia to "follow the Change an appointment skill", but only the bundled agent's
`base.md` included the skill pages, so the identity check and the notices it describes never reached
a published agent. Each enabled skill's page now does; a switched-off one stays out.
"""

from __future__ import annotations

from app.agents.config import AgentConfig
from app.workloads.conversational.surface import _instructions

_CONFIG = dict(
    name="Mia",
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
    treatments=[{"key": "klein", "name": "Medizinische Fußpflege klein", "external_id": "2"}],
)


def _prompt(**overrides) -> str:
    return _instructions("de", AgentConfig(**{**_CONFIG, **overrides}))


def test_the_change_skill_she_is_told_to_follow_is_in_her_prompt() -> None:
    prompt = _prompt()

    assert "follow the Change an appointment skill" in prompt
    assert "# Change an appointment" in prompt
    assert "Verify first, disclose nothing before you have." in prompt


def test_every_enabled_skill_has_its_page_and_a_routing_line() -> None:
    prompt = _prompt()

    for page in ("# Book an appointment", "# Change an appointment", "# Practice information"):
        assert page in prompt
    assert "- **Book an appointment** —" in prompt
    assert "{{" not in prompt


def test_a_switched_off_skill_is_not_described() -> None:
    prompt = _prompt(skills=["book_appointment", "practice_info"])

    assert "# Book an appointment" in prompt
    assert "# Change an appointment" not in prompt
    assert "Verify first" not in prompt


def test_an_agent_that_cannot_book_gets_no_booking_page() -> None:
    prompt = _prompt(booking_enabled=False)

    assert "# Book an appointment" not in prompt
    assert "# Practice information" in prompt


def test_one_kind_of_appointment_is_not_sent_to_a_tool_it_does_not_have() -> None:
    prompt = _prompt(treatments=[])

    assert "Only if this practice has a treatment list" in prompt
