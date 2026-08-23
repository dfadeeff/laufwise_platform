"""Pure task lifecycle guard; models propose transitions and the platform validates them."""

from __future__ import annotations

from typing import Literal

TaskStatus = Literal[
    "pending", "live", "action_required", "completed", "failed", "cancelled"
]

_ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    "pending": {"live", "failed", "cancelled"},
    "live": {"action_required", "completed", "failed", "cancelled"},
    "action_required": {"live", "cancelled"},
    "completed": {"live"},
    "failed": {"live"},
    "cancelled": {"live"},
}


def validate_transition(current: str, requested: TaskStatus) -> None:
    allowed = next((values for key, values in _ALLOWED.items() if key == current), None)
    if allowed is None or requested not in allowed:
        raise ValueError(f"illegal task transition: {current} -> {requested}")
