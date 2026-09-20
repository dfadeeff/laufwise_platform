"""Run against an explicitly designated temporary database, never a configured customer DB."""

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.agents import service
from app.agents.config import AgentConfig
from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.db.models import Tenant, Connection
from app.db.seed import seed_templates_from_dir
from app.db.session import get_session
from app.main import create_app

URL = os.environ.get("STUDIO_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="requires STUDIO_TEST_DATABASE_URL pointing to an isolated database"
)


@pytest.fixture
def workspace(monkeypatch):
    engine = create_async_engine(URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner_id, foreign_id = uuid.uuid4(), uuid.uuid4()
    conn_id, foreign_conn_id = uuid.uuid4(), uuid.uuid4()

    async def seed():
        async with maker() as s:
            s.add_all(
                [
                    Tenant(id=owner_id, name="workspace test"),
                    Tenant(id=foreign_id, name="other test"),
                ]
            )
            await s.flush()
            s.add_all(
                [
                    Connection(
                        id=conn_id,
                        tenant_id=owner_id,
                        type="calendar",
                        adapter="thevea",
                        config={"rooms": {"MA1": 1, "MA2": 2, "MA3": 3}},
                    ),
                    Connection(
                        id=foreign_conn_id,
                        tenant_id=foreign_id,
                        type="calendar",
                        adapter="thevea",
                        config={},
                    ),
                ]
            )
            await s.commit()
            await seed_templates_from_dir(s, Path(__file__).parents[1] / "runbooks")

    asyncio.run(seed())
    app = create_app()
    owner = SimpleNamespace(id=owner_id)
    app.dependency_overrides[current_tenant] = lambda: owner

    async def session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = session
    client = TestClient(app)
    yield client, owner, conn_id, foreign_conn_id, maker
    asyncio.run(engine.dispose())


def complete_config():
    return AgentConfig(
        name="Reception",
        practice_name="Test practice",
        street="Test street 1",
        postcode="12345",
        city="Test city",
        phone="+493012345678",
        recipients=["staff@example.org"],
        consent_policy_id="test-policy",
        treatments=[{"key": "consultation", "name": "Consultation", "price_eur": 45}],
    ).model_dump()


def test_save_publish_and_restore_are_distinct_and_tenant_scoped(workspace):
    client, owner, connection, foreign, maker = workspace
    agent = client.post("/api/v1/agents").json()
    path = "/api/v1/agents/" + agent["id"]
    assert agent["published_instance_id"] is None
    config = complete_config()
    result = client.post(path + "/draft", json={"generation": 1, "config": config})
    assert result.status_code == 200, result.text
    assert result.json()["generation"] == 2
    assert client.post(path + "/draft", json={"generation": 1, "config": config}).status_code == 409
    published = client.post(path + "/publish", json={"generation": 2})
    assert published.status_code == 200, published.text
    first = published.json()["published_instance_id"]
    assert published.json()["channel"] is None
    assert len(published.json()["history"]) == 1
    # Publishing identical content is idempotent.
    assert (
        client.post(path + "/publish", json={"generation": 2}).json()["published_instance_id"]
        == first
    )
    config["name"] = "Edited draft"
    client.post(path + "/draft", json={"generation": 2, "config": config}).raise_for_status()
    current = client.get(path).json()
    assert current["config"]["name"] == "Edited draft"
    assert current["history"][0]["config"]["name"] == "Reception"
    assert current["published_instance_id"] == first
    assert client.post(path + "/check", json={"connection_id": str(foreign)}).status_code == 404
    owner.id = uuid.uuid4()
    assert client.get(path).status_code == 404
    assert client.post(path + "/publish", json={"generation": 3}).status_code == 404


def test_rehearsal_uses_pinned_snapshot_and_does_not_publish(workspace, monkeypatch):
    client, owner, _, _, maker = workspace
    for key in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(settings, key, "test-only")
    agent = client.post("/api/v1/agents").json()
    response = client.post(
        "/api/v1/conversational/sessions",
        json={"agent_id": agent["id"], "generation": 1, "language": "en"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    call = client.get("/api/v1/conversations/" + data["conversation_id"]).json()
    assert call["metadata"]["mode"] == "rehearsal"
    assert call["metadata"]["revision"] == 1
    assert client.get("/api/v1/agents/" + agent["id"]).json()["published_instance_id"] is None

    async def read():
        async with maker() as s:
            instance = await repo.get_instance(s, uuid.UUID(call["instance_id"]), owner.id)
            assert instance.snapshot_kind == "test"
            assert instance.status == "draft"
            assert instance.runtime_config["base_prompt"]

    asyncio.run(read())


def test_activation_verifies_target_and_pause_preserves_ownership(workspace, monkeypatch):
    client, owner, connection, _, maker = workspace
    monkeypatch.setattr(service, "check_calendar", lambda *args: {"ok": True})

    async def verify(number, tenant_id):
        assert tenant_id == owner.id

    monkeypatch.setattr(service, "verify_number", verify)
    for key in (
        "smtp_host",
        "deepgram_api_key",
        "openai_api_key",
        "elevenlabs_api_key",
        "elevenlabs_voice_id",
    ):
        monkeypatch.setattr(settings, key, "test-only")
    agent = client.post("/api/v1/agents").json()
    path = "/api/v1/agents/" + agent["id"]
    client.post(
        path + "/draft", json={"generation": 1, "config": complete_config()}
    ).raise_for_status()
    published = client.post(path + "/publish", json={"generation": 2}).json()
    number = "+49" + str(uuid.uuid4().int)[:10]
    activation = {
        "instance_id": published["published_instance_id"],
        "connection_id": str(connection),
        "phone_number": number,
    }
    response = client.post(path + "/activate", json=activation)
    assert response.status_code == 200, response.text
    assert response.json()["channel"]["active"] is True
    assert client.post(path + "/pause").json()["channel"]["active"] is False
    assert client.get(path).json()["channel"]["phone_number"] == number
    assert client.post(path + "/activate", json=activation).status_code == 200


def test_new_agent_keeps_the_name_and_practice_the_customer_typed(workspace):
    """Creating from Studio names the agent up front; an empty POST still yields a blank draft."""
    client, *_ = workspace
    named = client.post(
        "/api/v1/agents",
        json={"name": "Empfang Nord", "practice_name": "Praxis Nord", "locale": "en"},
    )
    assert named.status_code == 200, named.text
    config = named.json()["config"]
    assert (config["name"], config["practice_name"], config["locale"]) == (
        "Empfang Nord",
        "Praxis Nord",
        "en",
    )
    assert client.post("/api/v1/agents").json()["config"]["name"] == "Receptionist"
    assert client.post("/api/v1/agents", json={"name": ""}).status_code == 422


def test_a_caller_is_remembered_for_one_agent_of_one_practice_and_can_be_forgotten(workspace):
    """Memory is scoped twice over — by practice and by agent — and erasable on request."""
    client, owner, _, foreign, maker = workspace
    agent = client.post("/api/v1/agents", json={"name": "Empfang"}).json()
    agent_id = uuid.UUID(agent["id"])
    projection = {
        "patient_id": 4711,
        "display_name": "Weber",
        "last_outcome": "TERMIN GEBUCHT",
        "verified": True,
    }

    async def remember_and_read():
        async with maker() as s:
            await repo.remember_caller(
                s,
                tenant_id=owner.id,
                agent_id=agent_id,
                caller_hash="hash-of-a-number",
                projection=projection,
            )
            mine = await repo.recall_caller(
                s, tenant_id=owner.id, agent_id=agent_id, caller_hash="hash-of-a-number"
            )
            # The same hash, a different practice: nothing.
            theirs = await repo.recall_caller(
                s,
                tenant_id=uuid.uuid4(),
                agent_id=agent_id,
                caller_hash="hash-of-a-number",
            )
            return mine, theirs

    mine, theirs = asyncio.run(remember_and_read())
    assert (mine.patient_id, mine.display_name, mine.call_count) == (4711, "Weber", 1)
    assert mine.verified_at is not None
    assert theirs is None

    # A second call from the same person updates rather than duplicating.
    again, _ = asyncio.run(remember_and_read())
    assert again.call_count == 2

    erased = client.request("DELETE", f"/api/v1/agents/{agent['id']}/callers")
    assert erased.status_code == 200, erased.text
    assert erased.json()["forgotten"] == 1

    async def read_back():
        async with maker() as s:
            return await repo.recall_caller(
                s, tenant_id=owner.id, agent_id=agent_id, caller_hash="hash-of-a-number"
            )

    assert asyncio.run(read_back()) is None
    assert foreign is not None  # fixture sanity: the foreign tenant exists and saw none of this
