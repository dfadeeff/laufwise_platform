"""Tenancy isolation (ADR-0003 D2) — two Clerk orgs map to two tenants; each sees only its own
deployed instances and cannot read, pause, or run another tenant's instance.

The Clerk token is not minted here (that needs a browser); instead `current_tenant` is overridden
per request to stand in for two verified principals. What this proves is the part that must hold
regardless of the token: the repo/route scoping. A cross-tenant instance id resolves to 404.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.db.models import AgentInstance, Connection, Template, Tenant
from app.main import app

_NAME = "tenancy_test"
_ORG_A = "org_test_aaa"
_ORG_B = "org_test_bbb"


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


def _published_template() -> None:
    """Seed one published template both tenants can deploy from (catalog is global)."""
    contract = {
        "template": _NAME,
        "risk": "low",
        "agent_class": "conversational",
        "parameters": {"persona": {"type": "text", "required": True}},
        "required_connections": ["calendar"],
        "state": {"calendar": {"provider": "memory"}},
        "steps": [
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
    client.post("/api/v1/templates", json={"contract": contract})
    client.post(f"/api/v1/templates/{_NAME}/publish")


def _as_org(clerk_org_id: str):
    """Override current_tenant to the tenant for a Clerk org (stands in for a verified token)."""

    async def _dep() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                tenant = await repo.tenant_for_org(s, clerk_org_id, name=clerk_org_id)
                await s.commit()
                return tenant
        finally:
            await engine.dispose()

    return _dep


def _cleanup() -> None:
    async def go(s: AsyncSession):
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
        for org in (_ORG_A, _ORG_B):
            t = (
                await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))
            ).scalar_one_or_none()
            if t is not None:
                await s.execute(delete(Connection).where(Connection.tenant_id == t.id))
                await s.delete(t)
        await s.commit()

    _run_db(go)


@pytest.fixture(autouse=True)
def _isolated():
    _cleanup()
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _deploy() -> str:
    r = client.post(
        "/api/v1/instances",
        json={"template": _NAME, "param_values": {"persona": "x"}},
    )
    assert r.status_code == 200, r.text
    return r.json()["instance_id"]


def test_two_orgs_are_isolated() -> None:
    _published_template()

    # Org A deploys an instance.
    app.dependency_overrides[current_tenant] = _as_org(_ORG_A)
    a_instance = _deploy()
    a_list = client.get("/api/v1/instances").json()
    assert [i["instance_id"] for i in a_list] == [a_instance]

    # Org B deploys its own — and sees ONLY its own, not A's.
    app.dependency_overrides[current_tenant] = _as_org(_ORG_B)
    b_instance = _deploy()
    b_list = client.get("/api/v1/instances").json()
    assert [i["instance_id"] for i in b_list] == [b_instance]
    assert a_instance not in {i["instance_id"] for i in b_list}

    # Org B cannot reach A's instance: run + pause both 404 (not 403 — it doesn't exist for B).
    assert client.post(f"/api/v1/instances/{a_instance}/runs", json={"case": {}}).status_code == 404
    assert client.post(f"/api/v1/instances/{a_instance}/pause").status_code == 404

    # A still sees only its own after B's activity.
    app.dependency_overrides[current_tenant] = _as_org(_ORG_A)
    a_list2 = client.get("/api/v1/instances").json()
    assert [i["instance_id"] for i in a_list2] == [a_instance]