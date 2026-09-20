"""Customer configuration and session isolation invariants."""

import pytest
from pydantic import ValidationError
from app.agents.config import AgentConfig
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
