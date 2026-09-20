"""Customer configuration and session isolation invariants."""

import pytest
from pydantic import ValidationError
from app.agents.config import AgentConfig
from app.workloads.conversational.capabilities import resolve
from app.schemas.connection import ConnectionCreate


def test_new_practice_never_inherits_customer_identity_or_recipients():
    config = AgentConfig()
    practice = config.to_practice()
    assert practice.name == "Your practice"
    assert practice.recipients == ()
    assert practice.email == ""
    assert config.publish_issues()


def test_voice_configuration_rejects_unusable_schedule():
    with pytest.raises(ValidationError):
        AgentConfig(open_from="18:00", open_until="09:00")
    with pytest.raises(ValidationError):
        AgentConfig(timezone="not/a-zone")


def test_room_mapping_is_structured_and_validated():
    request = ConnectionCreate(credentials={}, config={"rooms": {"MA1": 42}})
    assert request.config["rooms"]["MA1"] == 42
    with pytest.raises(ValidationError):
        ConnectionCreate(credentials={}, config={"rooms": {"MA1": -1}})


def test_practice_settings_are_the_runtime_truth():
    config = AgentConfig(
        practice_name="Praxis Nord",
        open_from="08:00",
        open_until="16:00",
        resources=["room_a"],
        recipients=["staff@example.org"],
    )
    practice = config.to_practice()
    assert practice.name == "Praxis Nord"
    assert practice.schedule.resources == ("room_a",)
    assert practice.schedule.periods[0].start.hour == 8
    assert practice.recipients == ("staff@example.org",)


def test_stale_draft_cannot_overwrite_newer_work():
    from types import SimpleNamespace
    from app.agents.service import check_generation, StudioError

    with pytest.raises(StudioError, match="another session"):
        check_generation(SimpleNamespace(generation=3), 2)


def test_rehearsal_never_resolves_a_bound_live_calendar(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.agents import runtime

    async def forbidden(*args, **kwargs):
        raise AssertionError("rehearsal accessed a production connection")

    monkeypatch.setattr(runtime, "resolve_calendar", forbidden)
    instance = SimpleNamespace(
        runtime_config=AgentConfig(practice_name="Test practice").model_dump()
    )
    calendar, mode, config = asyncio.run(runtime.prepare_voice(None, instance, rehearsal=True))
    assert mode == "sandbox"
    assert config.practice_name == "Test practice"


def test_live_session_rejects_legacy_and_draft_snapshots():
    import asyncio
    from types import SimpleNamespace
    from app.agents.runtime import prepare_voice
    from app.agents.service import StudioError

    for row in [
        SimpleNamespace(runtime_config=None),
        SimpleNamespace(runtime_config=AgentConfig().model_dump(), snapshot_kind="test"),
    ]:
        with pytest.raises(StudioError):
            asyncio.run(prepare_voice(None, row, rehearsal=False))


def test_media_token_is_single_use():
    from app.workloads.conversational.sessions import VoiceSessions

    sessions = VoiceSessions()
    token = sessions.create("tenant")
    sessions.authorize(token)
    with pytest.raises(KeyError):
        sessions.authorize(token)


def test_custom_practice_prompt_has_no_previous_customer_facts():
    from app.workloads.conversational.surface import _instructions

    text = _instructions("en", AgentConfig(practice_name="Praxis Nord"))
    assert "Praxis Nord" in text
    assert "Healthy Feet" not in text
    assert "€69" not in text


# --- capabilities: what the agent can do, resolved once ----------------------------------------


def test_an_agent_published_before_capabilities_existed_keeps_all_of_them():
    """The field defaults to None, and None means every skill — an old snapshot cannot be
    silently lobotomised by a deploy."""
    old_snapshot = {"name": "Empfang", "practice_name": "Praxis", "booking_enabled": True}
    config = AgentConfig.model_validate(old_snapshot)

    assert config.skills is None
    assert resolve(config).names == resolve().names


def test_a_switched_off_capability_takes_its_tools_with_it():
    """The guarantee is absence, not refusal: the argument does not exist for the model to call."""
    powers = resolve(AgentConfig(skills=["practice_info"], booking_enabled=False))

    assert powers.names == ("practice_info",)
    assert "appointment_book" not in powers.tools
    assert "search_availability" not in powers.tools
    assert "create_callback_request" in powers.tools  # always reachable — a person can be asked for


def test_booking_switched_off_also_withdraws_the_tools_that_lead_to_a_booking():
    """Collecting a birth date and a confirmation with no way to book is a promise nothing keeps."""
    powers = resolve(AgentConfig(booking_enabled=False))

    assert not {"appointment_set_details", "appointment_confirm"} & set(powers.tools)


def test_a_capability_that_no_longer_exists_is_dropped_rather_than_raised():
    """A skill renamed in a later release must not take a published agent's phone line down."""
    config = AgentConfig(skills=["practice_info", "removed_in_a_later_release"])

    assert resolve(config).names == ("practice_info",)


def test_the_publish_gate_catches_what_the_runtime_forgives():
    unknown = AgentConfig(skills=["practice_info", "removed_in_a_later_release"])
    contradiction = AgentConfig(skills=["practice_info"], booking_enabled=True)

    assert any("no longer exists" in issue for issue in unknown.publish_issues())
    assert any("booking capability is off" in issue for issue in contradiction.publish_issues())


def test_capabilities_cannot_be_granted_here_only_taken_away():
    """Whichever control says no wins, so the two can never disagree at runtime."""
    every_tool = set(resolve().tools)

    for config in (AgentConfig(), AgentConfig(booking_enabled=False), AgentConfig(skills=["practice_info"])):
        assert set(resolve(config).tools) <= every_tool
