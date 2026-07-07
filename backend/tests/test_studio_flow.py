"""Stages 3+4 — the full Studio loop over HTTP against the live DB (skips if unreachable):

author a draft -> gate refuses the ungoverned version -> publish v1 (immutable) ->
edit forks a v2 draft while v1 stays byte-identical -> deploy an instance (param-validated,
pinned to template@version, simulated connection auto-bound) -> a manually-triggered run of
that instance executes the governed contract and persists with the instance id ->
pausing the instance refuses further runs.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import AgentInstance, EpisodeEvent, Run, Template
from app.main import app

_NAME = "studio_flow_test"


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _db_reachable() -> bool:
    try:
        _run_db(lambda s: s.execute(select(1)))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Supabase DB not reachable")

client = TestClient(app)


def _contract(persona_required: bool = True) -> dict[str, Any]:
    return {
        "template": _NAME,
        "risk": "low",
        "agent_class": "conversational",
        "parameters": {
            "persona": {"type": "text", "required": persona_required},
            "locale": {"type": "enum", "options": ["de", "en"], "default": "de"},
        },
        "required_connections": ["calendar"],
        "state": {"calendar": {"provider": "memory"}},
        "steps": [
            {"id": "greet", "kind": "trace", "description": "greet as {{persona}}"},
            {
                "id": "book_slot",
                "kind": "enforced",
                "tools": ["book_appointment"],
                "preconditions": [{"check": "calendar.has_free_slot == true"}],
                "execute": {"adapter": "registry", "tool": "book_appointment"},
                "postconditions": [{"check": "calendar.booking_confirmed == true"}],
            },
        ],
    }


def _cleanup() -> None:
    async def go(s: AsyncSession):
        instance_ids = list(
            (
                await s.execute(
                    select(AgentInstance.id)
                    .join(Template, Template.id == AgentInstance.template_id)
                    .where(Template.name == _NAME)
                )
            ).scalars()
        )
        run_ids = list(
            (
                await s.execute(select(Run.id).where(Run.template_name == _NAME))
            ).scalars()
        )
        if run_ids:
            await s.execute(delete(EpisodeEvent).where(EpisodeEvent.run_id.in_(run_ids)))
            await s.execute(delete(Run).where(Run.id.in_(run_ids)))
        for iid in instance_ids:
            instance = await s.get(AgentInstance, iid)
            if instance is not None:
                await s.delete(instance)  # cascades instance_connection rows
        await s.execute(delete(Template).where(Template.name == _NAME))
        await s.commit()

    _run_db(go)


@pytest.fixture(autouse=True)
def _isolated():
    _cleanup()
    yield
    _cleanup()


def test_studio_loop_end_to_end() -> None:
    # 1. An ungoverned draft saves fine (drafts are workbenches) but publish is REFUSED.
    bad = _contract()
    bad["steps"][1]["postconditions"] = []
    assert client.post("/api/v1/templates", json={"contract": bad}).status_code == 200
    refused = client.post(f"/api/v1/templates/{_NAME}/publish")
    assert refused.status_code == 422
    assert any("no postcondition" in v for v in refused.json()["detail"]["violations"])

    # 2. Fix the draft; the gate passes; v1 publishes and is immutable.
    assert client.post("/api/v1/templates", json={"contract": _contract()}).status_code == 200
    published = client.post(f"/api/v1/templates/{_NAME}/publish")
    assert published.status_code == 200, published.text
    assert published.json() == {"name": _NAME, "version": 1, "status": "published"}

    # 3. Editing forks a v2 draft; v1 stays published and byte-identical.
    edited = _contract()
    edited["risk"] = "medium"
    v2 = client.post("/api/v1/templates", json={"contract": edited}).json()
    assert (v2["version"], v2["status"]) == (2, "draft")
    v1 = client.get(f"/api/v1/templates/{_NAME}", params={"version": 1}).json()
    assert (v1["status"], v1["risk"]) == ("published", "low")

    # 4. Deploy: missing required param is refused with a precise message.
    refused = client.post("/api/v1/instances", json={"template": _NAME, "param_values": {}})
    assert refused.status_code == 422
    assert any(
        "required parameter 'persona'" in v for v in refused.json()["detail"]["violations"]
    )

    # 5. Valid deploy pins template@v1 (v2 is only a draft) and auto-binds the
    #    simulated calendar connection.
    deployed = client.post(
        "/api/v1/instances",
        json={"template": _NAME, "param_values": {"persona": "Dr. Test"}},
    )
    assert deployed.status_code == 200, deployed.text
    instance = deployed.json()
    assert instance["template_version"] == 1
    assert instance["status"] == "deployed"
    assert "calendar" in instance["connections"]

    listed = client.get("/api/v1/instances").json()
    assert any(i["instance_id"] == instance["instance_id"] for i in listed)

    # 6. A manually-triggered run of the instance executes the governed contract.
    run = client.post(
        f"/api/v1/instances/{instance['instance_id']}/runs",
        json={"case": {"calendar": {"has_free_slot": True, "booking_confirmed": False}}},
    )
    assert run.status_code == 200, run.text
    steps = {s["step_id"]: s for s in run.json()["steps"]}
    assert steps["book_slot"]["status"] == "ok"

    detail = client.get(f"/api/v1/runs/{run.json()['run_id']}").json()
    assert detail["runbook"] == _NAME and detail["version"] == 1

    def _instance_id_of_run(s: AsyncSession):
        return s.get(Run, uuid.UUID(run.json()["run_id"]))

    persisted = _run_db(_instance_id_of_run)
    assert persisted.instance_id.hex == instance["instance_id"]

    # 7. The governed loop still bites: a no-slot case BLOCKs through the instance path.
    blocked = client.post(
        f"/api/v1/instances/{instance['instance_id']}/runs",
        json={"case": {"calendar": {"has_free_slot": False, "booking_confirmed": False}}},
    )
    blocked_steps = {s["step_id"]: s for s in blocked.json()["steps"]}
    assert blocked_steps["book_slot"]["status"] == "blocked"

    # 8. Pausing the instance refuses further runs.
    paused = client.post(f"/api/v1/instances/{instance['instance_id']}/pause")
    assert paused.json()["status"] == "paused"
    refused_run = client.post(
        f"/api/v1/instances/{instance['instance_id']}/runs", json={"case": {}}
    )
    assert refused_run.status_code == 409