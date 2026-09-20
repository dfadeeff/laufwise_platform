"""Wire models for agent instances (Stage 4 configuration tier)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DeployRequest(BaseModel):
    """Deploy an instance: pick a published template, fill its parameter form.
    `version` omitted -> latest published. `connections` maps a required-connection role to
    an existing Connection id; unbound roles fall back to the simulated connection (v1)."""

    template: str
    version: int | None = None
    param_values: dict[str, Any] = Field(default_factory=dict)
    connections: dict[str, str] = Field(default_factory=dict)
    phone_number: str | None = None


class InstanceSummary(BaseModel):
    agent_id: str | None = None
    instance_id: str
    template: str
    template_version: int
    status: str  # draft | deployed | paused
    param_values: dict[str, Any]
    connections: dict[str, str]  # role -> connection id
    phone_number: str | None = None
    # The named schedule this instance is armed for, or null for manual-only (ADR-0010 D3).
    schedule: str | None = None
    created_at: datetime


class ScheduleRequest(BaseModel):
    """Arm an instance for a named schedule, or disarm it with `null`."""

    schedule: str | None = None


class InstanceRunRequest(BaseModel):
    """Manually trigger a governed run of a deployed instance against a case fixture."""

    case: dict[str, Any]