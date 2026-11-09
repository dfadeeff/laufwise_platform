"""An assessor whose lease was taken over cannot complete the replacement owner's work."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.assess import service


def test_a_slow_old_assessment_is_discarded_after_another_worker_claims_it(monkeypatch):
    record = SimpleNamespace(
        id="run", case_id="case", tenant_id="tenant", definition_version_id="definition",
        input_revision=1, state="suspended", trace=[{"step": "assess"}],
        outputs={"assess": {"pending": True, "claimed_at": "old-owner"}},
    )
    case = SimpleNamespace(id="case", agent_id="agent", input={}, input_revision=1)
    agent = SimpleNamespace(workflow_key="test", settings={})
    class Session:
        async def get(self, *a):
            return record
        async def refresh(self, *a):
            pass
    @asynccontextmanager
    async def factory():
        yield Session()
    monkeypatch.setattr(service, "_claim", AsyncMock(return_value=(record, "assess")))
    monkeypatch.setattr(service.repo, "get_workflow_case", AsyncMock(return_value=case))
    monkeypatch.setattr(service.repo, "get_workflow_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(service.repo, "workflow_case_files", AsyncMock(return_value=[]))
    monkeypatch.setattr(service.definitions, "workflow_of_version", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "find_workflow", lambda key: (None, object()))
    monkeypatch.setattr(service, "blob_store", lambda: object())
    async def model(*a):
        # A replacement worker claims the same suspended step while the old model is away.
        record.outputs = {"assess": {"pending": True, "claimed_at": "replacement-owner"}}
        return {}, []
    monkeypatch.setattr(service, "assess_case", model)
    monkeypatch.setattr(service.runs, "complete_assessment", lambda *a: pytest.fail("stale assessment committed"))
    asyncio.run(service.assess(factory, "run", model=object()))
