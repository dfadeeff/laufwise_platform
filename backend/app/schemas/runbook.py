"""Wire models describing a runbook (the process contract) at the API boundary.

These are deliberately thin — the authoritative runbook model lives in the `laufwise`
spec package. These DTOs are what the API exposes to the frontend, so the console can
list and inspect contracts without importing the engine.
"""

from __future__ import annotations

from pydantic import BaseModel


class RunbookSummary(BaseModel):
    """Listing view of a runbook."""

    name: str
    version: int
    risk: str
    step_count: int


class RunbookDetail(RunbookSummary):
    """Full view including the ordered step ids."""

    step_ids: list[str]