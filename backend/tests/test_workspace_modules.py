"""A workspace runs the modules it chose, and only those (ADR-0028).

A new organization has chosen nothing; it is offered every installed module. In a workspace that
does not run `praxis`, the practice's voice agents and workflows are refused by the server, not
merely hidden by the Studio. The legacy `default` tenant is a practice, as it always was.
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
from app.db.models import Tenant
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
        await s.execute(delete(Tenant).where(Tenant.clerk_org_id == _ORG))
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


def test_choosing_a_module_is_recorded_once(new_org) -> None:
    assert client.post("/api/v1/workspace/modules", json={"key": "immobilien"}).status_code == 200
    assert client.post("/api/v1/workspace/modules", json={"key": "immobilien"}).status_code == 200
    assert _running() == ["immobilien"]
    assert client.post("/api/v1/workspace/modules", json={"key": "nope"}).status_code == 404


def test_a_property_workspace_cannot_create_a_voice_agent_or_deploy_a_practice_workflow(
    new_org,
) -> None:
    client.post("/api/v1/workspace/modules", json={"key": "immobilien"})

    agent = client.post("/api/v1/agents")
    deploy = client.post(
        "/api/v1/instances", json={"template": "calendar_import", "param_values": {}}
    )

    assert agent.status_code == 403
    assert deploy.status_code == 403
    assert "Medical practice" in deploy.json()["detail"]


def test_an_unconfigured_workspace_is_refused_too(new_org) -> None:
    assert client.post("/api/v1/agents").status_code == 403


def test_adding_the_practice_module_opens_its_workflows(new_org) -> None:
    client.post("/api/v1/workspace/modules", json={"key": "immobilien"})
    client.post("/api/v1/workspace/modules", json={"key": "praxis"})

    # A version that does not exist: past the module check, the deploy stops at "no such
    # template" without leaving an instance behind.
    deploy = client.post(
        "/api/v1/instances",
        json={"template": "calendar_import", "version": 999, "param_values": {}},
    )

    assert _running() == ["immobilien", "praxis"]
    assert deploy.status_code == 404


def test_a_template_no_module_claims_is_not_restricted(new_org) -> None:
    deploy = client.post(
        "/api/v1/instances", json={"template": "authored_in_the_studio", "param_values": {}}
    )
    assert deploy.status_code == 404  # unknown template, not a module refusal


def test_the_default_tenant_is_a_practice() -> None:
    assert "praxis" in _running()


def test_a_property_workspace_cannot_spend_or_act_for_voice(new_org) -> None:
    """Red flag C9: a number costs money, a session runs a voice agent, and the service catalogue
    is what a voice agent books from. Creating the agent was the only refusal."""
    client.post("/api/v1/workspace/modules", json={"key": "immobilien"})

    refused = [
        client.post("/api/v1/numbers/claim", json={"country": "DE"}),
        client.post("/api/v1/conversational/sessions", json={}),
        client.post("/api/v1/services", json={"name": "Fußpflege"}),
        client.post("/api/v1/services/recognise", json={"said": "Fußpflege"}),
    ]

    assert [r.status_code for r in refused] == [403, 403, 403, 403]
    assert client.get("/api/v1/services").status_code == 200  # reading stays open
