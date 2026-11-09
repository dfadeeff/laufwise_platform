"""An external tool never runs without durable intent; later failures keep that evidence."""

import asyncio
import uuid
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from laufwise.adapters.base import StepOutcome

from app.control_plane import runtime
from app.db import repo
from app.templates.contract import TemplateContract


@pytest.fixture
def journal(monkeypatch, tmp_path):
    records = []
    contract = TemplateContract.model_validate({
        "template": "durability", "version": 1, "risk": "low", "agent_class": "workflow",
        "steps": [{"id": "write", "kind": "enforced", "tools": ["write"],
                   "execute": {"adapter": "registry", "tool": "write"}}],
    })
    template = SimpleNamespace(contract=contract.model_dump(by_alias=True))
    class Session:
        async def get(self, *args):
            return template
    @asynccontextmanager
    async def factory():
        yield Session()
    async def begin(session, **kwargs):
        records.append(("begin", kwargs))
    async def append(session, **kwargs):
        records.append((kwargs["kind"], kwargs["payload"]))
    async def finish(session, **kwargs):
        records.append(("finish", kwargs))
    monkeypatch.setattr(runtime, "get_sessionmaker", lambda: factory, raising=False)
    monkeypatch.setattr(repo, "begin_run", begin, raising=False)
    monkeypatch.setattr(repo, "append_run_event", append, raising=False)
    monkeypatch.setattr(repo, "complete_run", finish, raising=False)
    instance = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), template_id=uuid.uuid4(), param_values={})
    return records, Session(), instance, runtime.Runtime(str(tmp_path))


def _tools(monkeypatch, tool):
    async def resolve(*args):
        return SimpleNamespace(providers={}, tools={"write": tool}, close=lambda: None)
    monkeypatch.setattr(runtime, "resolve_connectors", resolve)


def test_intent_and_tool_receipt_are_committed_before_the_next_phase(monkeypatch, journal):
    records, session, instance, runner = journal
    def tool(*args):
        assert records[0][0] == "begin"
        assert records[-1][0] == "action_intent"
        return StepOutcome(ok=True)
    _tools(monkeypatch, tool)
    result = asyncio.run(runner.run_instance(session, instance, {"source_ref": "source-17"}))
    kinds = [kind for kind, _ in records]
    assert kinds == ["begin", "action_intent", "action_returned", "trace", "finish"]
    assert records[0][1]["run_id"].hex == result.run_id
    assert records[1][1]["step_id"] == "write"
    assert records[1][1]["tool"] == "write"


def test_a_failed_intent_commit_prevents_the_external_action(monkeypatch, journal):
    _, session, instance, runner = journal
    async def unavailable(*args, **kwargs):
        raise RuntimeError("journal unavailable")
    monkeypatch.setattr(repo, "append_run_event", unavailable)
    _tools(monkeypatch, lambda *a: pytest.fail("unrecorded external write"))
    with pytest.raises(RuntimeError, match="journal unavailable"):
        asyncio.run(runner.run_instance(session, instance, {}))


def test_a_failed_final_commit_does_not_erase_the_action_evidence(monkeypatch, journal):
    records, session, instance, runner = journal
    _tools(monkeypatch, lambda *a: StepOutcome(ok=True))
    async def unavailable(*args, **kwargs):
        raise RuntimeError("final commit unavailable")
    monkeypatch.setattr(repo, "complete_run", unavailable)
    with pytest.raises(RuntimeError, match="final commit unavailable"):
        asyncio.run(runner.run_instance(session, instance, {}))
    assert [kind for kind, _ in records] == ["begin", "action_intent", "action_returned", "trace"]


def test_run_intent_keeps_a_hash_instead_of_raw_patient_data():
    added = []
    class Session:
        def add(self, run):
            added.append(run)
        async def commit(self):
            pass
    asyncio.run(repo.begin_run(
        Session(), run_id=uuid.uuid4(), template_name="test", template_version=1,
        tenant_id=uuid.uuid4(), instance_id=None,
        case={"appointment": {"raw": {"patient": "Private Patient", "notes": "Private Medical Note"}}},
    ))
    intent = added[0].events[0].payload
    assert len(intent["input_sha256"]) == 64
    assert "Private" not in json.dumps(intent)


def test_the_run_api_exposes_evidence_from_before_a_crash(monkeypatch):
    from app.api.v1 import runs
    owner, rid = uuid.uuid4(), uuid.uuid4()
    async def get(session, run_id, *, tenant_id):
        assert run_id == rid and tenant_id == owner
        return SimpleNamespace(
            id=rid, template_name="test", template_version=1, lifecycle="unverified",
            status="state_unavailable", started_at=datetime.now(timezone.utc), trace_ref=None,
            events=[SimpleNamespace(seq=1, kind="action_intent", payload={"tool": "write"})],
        )
    monkeypatch.setattr(repo, "get_run", get)
    out = asyncio.run(runs.get_run(str(rid), object(), SimpleNamespace(id=owner)))
    assert out.lifecycle == "unverified" and out.steps == []
    assert out.journal == [{"seq": 1, "kind": "action_intent", "payload": {"tool": "write"}}]
