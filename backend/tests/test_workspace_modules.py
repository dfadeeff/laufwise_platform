"""A workspace runs the modules it has used (ADR-0028, amended 19 October 2026).

There is no up-front choice: a new organization runs nothing and is offered every module's
agents; creating an agent or deploying a workflow from a module records that module, and from then
on the workspace is offered its own modules' agents. The legacy `default` tenant is a practice.
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
from app.db.models import StudioAgent, Tenant
from app.kernel.modules import installed, module_to_add
from app.main import app

_ORG = "org_test_modules"


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


async def _org_tenant() -> Tenant:
    engine = create_async_engine(settings.sqlalchemy_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            tenant = await repo.tenant_for_org(s, _ORG, name=_ORG)
            await s.commit()
            return tenant
    finally:
        await engine.dispose()


def _cleanup() -> None:
    async def go(s: AsyncSession):
        tenant = (
            await s.execute(select(Tenant).where(Tenant.clerk_org_id == _ORG))
        ).scalar_one_or_none()
        if tenant is not None:
            await s.execute(delete(StudioAgent).where(StudioAgent.tenant_id == tenant.id))
            await s.delete(tenant)
        await s.commit()

    _run_db(go)


@pytest.fixture
def new_org():
    _cleanup()
    app.dependency_overrides[current_tenant] = _org_tenant
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _running() -> list[str]:
    return [m["key"] for m in client.get("/api/v1/workspace/modules").json()["running"]]


def test_a_new_organization_has_chosen_nothing_and_is_offered_every_module(new_org) -> None:
    body = client.get("/api/v1/workspace/modules").json()

    assert body["running"] == []
    assert [m["key"] for m in body["available"]] == ["praxis", "immobilien"]
    offered = client.get("/api/v1/workflow-agents/workflows").json()
    assert [w["key"] for w in offered] == ["betriebskosten"]


def test_choosing_a_module_is_recorded_once(new_org) -> None:
    assert client.post("/api/v1/workspace/modules", json={"key": "immobilien"}).status_code == 200
    assert client.post("/api/v1/workspace/modules", json={"key": "immobilien"}).status_code == 200
    assert _running() == ["immobilien"]
    assert client.post("/api/v1/workspace/modules", json={"key": "nope"}).status_code == 404


def test_the_first_voice_agent_makes_a_new_workspace_a_practice(new_org) -> None:
    assert _running() == []

    assert client.post("/api/v1/agents").status_code == 200

    assert _running() == ["praxis"]


def test_a_deploy_that_fails_records_no_module(new_org) -> None:
    client.post("/api/v1/workspace/modules", json={"key": "immobilien"})

    deploy = client.post(
        "/api/v1/instances",
        json={"template": "calendar_import", "version": 999, "param_values": {}},
    )

    assert deploy.status_code == 404
    assert _running() == ["immobilien"]


def test_which_module_a_deploy_adds() -> None:
    praxis, immobilien = installed()["praxis"], installed()["immobilien"]
    assert module_to_add("calendar_import", [immobilien]) == "praxis"
    assert module_to_add("calendar_import", [praxis]) is None
    assert module_to_add("authored_in_the_studio", []) is None


def test_a_template_no_module_claims_is_not_restricted(new_org) -> None:
    deploy = client.post(
        "/api/v1/instances", json={"template": "authored_in_the_studio", "param_values": {}}
    )
    assert deploy.status_code == 404  # unknown template, not a module refusal


def test_the_default_tenant_is_a_practice() -> None:
    assert "praxis" in _running()
