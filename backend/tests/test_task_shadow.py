"""Release C proof that legacy import shadowing is opt-in and lifecycle-guarded."""

import pytest

from app.config import Settings
from app.tasks.state import validate_transition


def test_task_shadow_is_disabled_by_default() -> None:
    assert Settings(_env_file=None).task_shadow_enabled is False


def test_task_shadow_can_be_enabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("TASK_SHADOW_ENABLED", "true")
    assert Settings(_env_file=None).task_shadow_enabled is True


def test_shadow_task_accepts_only_legal_completion_paths() -> None:
    validate_transition("live", "completed")
    validate_transition("live", "failed")

    with pytest.raises(ValueError, match="pending -> completed"):
        validate_transition("pending", "completed")
