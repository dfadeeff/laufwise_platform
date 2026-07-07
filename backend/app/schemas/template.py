"""Wire models for templates at the API boundary (Stage 3 authoring tier).

The authoritative contract model is `templates/contract.py`; these DTOs wrap it with the
persistence-level fields (status, version) the Studio needs to render drafts vs. published.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TemplateSummary(BaseModel):
    """One template version in the catalog / authoring list."""

    name: str
    version: int
    status: str  # draft | published
    agent_class: str
    agent_surface: str | None = None
    risk: str
    step_count: int
    created_at: datetime


class TemplateDetail(TemplateSummary):
    """A full template version: the contract body the step-form editor edits."""

    contract: dict[str, Any]
    parameters: dict[str, Any]


class DraftRequest(BaseModel):
    """Save a draft: the full contract body as authored by the step-form editor.
    The server assigns the version (new name -> v1; existing name -> latest+1, or the
    current draft is replaced in place)."""

    contract: dict[str, Any]


class PublishResult(BaseModel):
    name: str
    version: int
    status: str