"""End-to-end connector wiring (ADR-0003): a deployed thevea-bound instance runs against the
thevea provider — connection create -> encrypted credential -> deploy binds it -> run resolves it
-> the engine's checks hit the thevea client (a mock transport, no real login) -> OK / BLOCKED /
REJECT. Proves the whole path without the captured GraphQL ops or a live account.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.connections import resolve
from app.db.models import AgentInstance, Connection, EpisodeEvent, Run, Template, Tenant
from app.main import app
from app.providers.thevea import TheveaClient

_NAME = "thevea_wiring_test"
_ORG = "org_thevea_wiring"
_START = "2026-07-14T09:00"
_KEY = Fernet.generate_key().decode()


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                return await fn(s)
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


def _thevea_transport(state: dict[str, bool]) -> httpx.MockTransport:
    """Stateful mock of thevea's GraphQL API: a Book mutation marks the slot taken (unless
    write_fails); Availability reflects current state for the requested window."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "Book" in query:
            if not state.get("write_fails"):
                state["booked"] = True
            return httpx.Response(200, json={"data": {"book": {"ok": True}}})
        if "Availability" in query:
            free = (not state.get("busy")) and (not state.get("booked"))
            return httpx.Response(200, json={"data": {"slots": [{"start": _START, "free": free}]}})
        return httpx.Response(200, json={"data": {"login": {"ok": True}}})

    return httpx.MockTransport(handler)


def _contract() -> dict[str, Any]:
    return {
        "template": _NAME,
        "risk": "medium",
        "agent_class": "conversational",
        "parameters": {"appointment_type": {"type": "enum", "options": ["routine"], "default": "routine"}},
        "required_connections": ["calendar"],
        "state": {"calendar": {"provider": "thevea", "query": "availability"}},
        "steps": [
            {
                "id": "book_slot",
                "kind": "enforced",
                "tools": ["book_appointment"],
                "preconditions": [{"check": "calendar.has_free_slot == true"}],
                "execute": {"adapter": "registry", "tool": "book_appointment"},
                "postconditions": [{"check": "calendar.slot_booked == true"}],
            },
        ],
    }


def _cleanup() -> None:
    async def go(s: AsyncSession):
        run_ids = list(
            (await s.execute(select(Run.id).where(Run.template_name == _NAME))).scalars()
        )
        if run_ids:
            await s.execute(delete(EpisodeEvent).where(EpisodeEvent.run_id.in_(run_ids)))
            await s.execute(delete(Run).where(Run.id.in_(run_ids)))
        iids = list(
            (
                await s.execute(
                    select(AgentInstance.id)
                    .join(Template, Template.id == AgentInstance.template_id)
                    .where(Template.name == _NAME)
                )
            ).scalars()
        )
        for iid in iids:
            inst = await s.get(AgentInstance, iid)
            if inst is not None:
                await s.delete(inst)
        await s.execute(delete(Template).where(Template.name == _NAME))
        t = (
            await s.execute(select(Tenant).where(Tenant.clerk_org_id == _ORG))
        ).scalar_one_or_none()
        if t is not None:
            await s.execute(delete(Connection).where(Connection.tenant_id == t.id))
            await s.delete(t)
        await s.commit()

    _run_db(go)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", _KEY)

    async def _tenant() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                from app.db import repo

                t = await repo.tenant_for_org(s, _ORG, name=_ORG)
                await s.commit()
                return t
        finally:
            await engine.dispose()

    app.dependency_overrides[current_tenant] = _tenant
    _cleanup()
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _deploy_thevea_instance(monkeypatch, state: dict[str, bool]) -> str:
    # thevea client factory -> mock transport (no real login).
    monkeypatch.setattr(
        resolve,
        "build_thevea_client",
        lambda base_url, username, password: TheveaClient(
            base_url, username, password, transport=_thevea_transport(state)
        ),
    )
    assert client.post("/api/v1/templates", json={"contract": _contract()}).status_code == 200
    assert client.post(f"/api/v1/templates/{_NAME}/publish").status_code == 200
    conn = client.post(
        "/api/v1/connections",
        json={"adapter": "thevea", "type": "calendar", "credentials": {"username": "u", "password": "p"}},
    )
    assert conn.status_code == 200, conn.text
    conn_id = conn.json()["id"]
    deployed = client.post(
        "/api/v1/instances",
        json={"template": _NAME, "param_values": {}, "connections": {"calendar": conn_id}},
    )
    assert deployed.status_code == 200, deployed.text
    return deployed.json()["instance_id"]


def _run(instance_id: str):
    return client.post(
        f"/api/v1/instances/{instance_id}/runs",
        json={"case": {"calendar": {"date": "2026-07-14", "type": "routine", "start": _START}}},
    ).json()


def test_free_slot_books_and_verifies_ok(monkeypatch):
    state = {"busy": False, "booked": False}
    result = _run(_deploy_thevea_instance(monkeypatch, state))
    assert [s["status"] for s in result["steps"]] == ["ok"]
    assert state["booked"] is True  # the tool actually wrote via the thevea client


def test_busy_slot_blocks_before_writing(monkeypatch):
    state = {"busy": True, "booked": False}
    result = _run(_deploy_thevea_instance(monkeypatch, state))
    step = result["steps"][0]
    assert step["status"] == "blocked"
    assert state["booked"] is False  # never attempted the write


def test_silent_write_failure_rejects(monkeypatch):
    # thevea accepts the write but never persists it -> postcondition re-query catches the lie.
    state = {"busy": False, "booked": False, "write_fails": True}
    result = _run(_deploy_thevea_instance(monkeypatch, state))
    assert result["steps"][0]["status"] == "rejected"