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


def test_a_call_admitted_by_one_process_opens_in_any_other_exactly_once(workspace, monkeypatch):
    """The webhook and the media socket can land on different replicas, or either side of a
    redeploy. The admission is a row, so any process can open it, and only one can."""
    from datetime import datetime, timedelta, timezone
    from urllib.parse import parse_qs, urlsplit

    from sqlalchemy import select

    from app.agents.runtime import open_voice_call
    from app.db.models import VoiceCallToken
    from app.workloads.conversational.sessions import token_digest

    client, owner, _, _, maker = workspace
    for key in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(settings, key, "test-only")
    agent = client.post("/api/v1/agents").json()
    data = client.post(
        "/api/v1/conversational/sessions",
        json={"agent_id": agent["id"], "generation": 1, "language": "en"},
    ).json()
    token = parse_qs(urlsplit(data["ws_url"]).query)["token"][0]

    async def scenario():
        async with maker() as s:
            stored = (await s.execute(select(VoiceCallToken.token_hash))).scalars().all()
        # Only the hash is kept: a leaked row cannot be replayed as a call.
        assert token_digest(token) in stored and token not in stored

        call = await open_voice_call(token, sessionmaker=maker)
        assert call.conversation_id.hex == data["conversation_id"]
        assert call.tenant_id == str(owner.id)
        assert call.language == "en" and call.rehearsal is True
        assert call.base_prompt and call.config is not None and call.calendar is not None
        with pytest.raises(KeyError):
            await open_voice_call(token, sessionmaker=maker)

        async with maker() as s:
            await repo.admit_voice_call(
                s,
                token_hash=token_digest("stale"),
                conversation_id=call.conversation_id,
                language="de",
                rehearsal=True,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        with pytest.raises(KeyError):
            await open_voice_call("stale", sessionmaker=maker)

    asyncio.run(scenario())


def test_each_agent_s_transcripts_are_deleted_after_its_own_retention_period(workspace):
    """One practice promised callers seven days, another thirty. A ten-day-old call is gone from
    the first and still readable in the second."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.models import ConversationEvent
    from app.workloads.conversational.retention import purge_once

    client, owner, _, _, maker = workspace

    def published(days):
        agent = client.post("/api/v1/agents").json()
        path = "/api/v1/agents/" + agent["id"]
        config = {**complete_config(), "transcript_retention_days": days}
        client.post(path + "/draft", json={"generation": 1, "config": config}).raise_for_status()
        response = client.post(path + "/publish", json={"generation": 2})
        assert response.status_code == 200, response.text
        return uuid.UUID(response.json()["published_instance_id"])

    short, long = published(7), published(30)

    async def scenario():
        async with maker() as s:
            calls = {}
            for instance_id in (short, long):
                call = await repo.create_conversation(
                    s, tenant_id=owner.id, instance_id=instance_id, channel="phone",
                    direction="inbound",
                )
                await repo.append_conversation_event(
                    s, conversation_id=call.id, kind="turn", payload={"text": "Guten Tag"}
                )
                call.started_at = datetime.now(timezone.utc) - timedelta(days=10)
                calls[instance_id] = call.id
            await s.commit()

        await purge_once(sessionmaker=maker)

        async with maker() as s:
            kept = set(
                (
                    await s.execute(
                        select(ConversationEvent.conversation_id).where(
                            ConversationEvent.conversation_id.in_(calls.values())
                        )
                    )
                ).scalars()
            )
        assert kept == {calls[long]}

    asyncio.run(scenario())


def test_a_voice_booking_s_governed_run_is_kept_with_the_call(workspace, monkeypatch, tmp_path):
    """A voice call's runs existed only as JSONL on the container's disk: the conversation's
    "what the engine checked" was always empty, and a redeploy erased the audit trail. Now the run,
    its steps and its full trace are stored, filed under the call's own practice."""
    from datetime import date, timedelta

    from app.workloads.conversational import booking as booking_module
    from app.workloads.conversational.booking import BookingSession
    from app.workloads.conversational.recording import ConversationRecorder

    client, owner, _, _, maker = workspace
    for key in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(settings, key, "test-only")
    monkeypatch.setattr(booking_module.settings, "runs_dir", str(tmp_path))
    agent = client.post("/api/v1/agents").json()
    data = client.post(
        "/api/v1/conversational/sessions", json={"agent_id": agent["id"], "generation": 1}
    ).json()
    recorder = ConversationRecorder(uuid.UUID(data["conversation_id"]))

    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    call = BookingSession("kept")
    call.set_details(
        first_name="Anna", last_name="Weber", date_of_birth="1971-04-12", phone="0176 4289 9911",
        service_key="medizinische_fusspflege", preferred_time=f"{day.isoformat()}T09:00",
    )
    call.find_patient()
    call.confirm("Anna Weber, neun Uhr.")
    result = call.book()
    assert result["status"] == "ok"

    async def record():
        await recorder.tool("appointment_book", {}, result)
        for execution in call.take_executions():
            await recorder.run(execution)

    asyncio.run(record())

    detail = client.get("/api/v1/conversations/" + data["conversation_id"]).json()
    assert detail["checks"] and all(c["run_id"] == result["run_id"] for c in detail["checks"])
    run = client.get("/api/v1/runs/" + result["run_id"]).json()
    assert run["status"] == "ok" and run["steps"] and run["trace"]
    assert run["trace"][-1]["tool_calls"]
    owner.id = uuid.uuid4()
    assert client.get("/api/v1/runs/" + result["run_id"]).status_code == 404


def test_a_practice_claims_a_pool_number_and_no_other_practice_can(workspace, monkeypatch):
    """Self-serve numbers against the real table: one owner per number, enforced by the database."""
    from app.agents import numbers

    client, owner, _, foreign, maker = workspace
    first, second = "+4989" + str(uuid.uuid4().int)[:8], "+4989" + str(uuid.uuid4().int)[:8]
    pointed: list[tuple[str, str]] = []

    async def account_numbers(*_args, **_kwargs):
        return [
            {"phone_number": n, "sid": "PN" + n[-6:], "friendly_name": n,
             "capabilities": {"voice": True}, "voice_url": ""}
            for n in (first, second)
        ]

    async def point_voice_at(_sid, _token, number_sid, url, **_kwargs):
        pointed.append((number_sid, url))

    monkeypatch.setattr(numbers.settings, "twilio_account_sid", "AC1")
    monkeypatch.setattr(numbers.settings, "twilio_auth_token", "secret")
    monkeypatch.setattr(numbers.telephony, "account_numbers", account_numbers)
    monkeypatch.setattr(numbers.telephony, "point_voice_at", point_voice_at)

    offered = {n["number"] for n in client.get("/api/v1/numbers").json()["available"]}
    assert {first, second} <= offered

    claimed = client.post("/api/v1/numbers/claim", json={"number": first})
    assert claimed.status_code == 200, claimed.text
    assert first in claimed.json()["owned"]
    assert pointed[-1][1].endswith("/api/v1/telephony/incoming")

    practice = owner.id
    owner.id = _other_tenant(maker)
    assert client.post("/api/v1/numbers/claim", json={"number": first}).status_code == 409
    assert first not in {n["number"] for n in client.get("/api/v1/numbers").json()["available"]}
    assert client.post("/api/v1/numbers/release", json={"number": first}).status_code == 404

    owner.id = practice
    released = client.post("/api/v1/numbers/release", json={"number": first})
    assert released.status_code == 200 and first not in released.json()["owned"]


def _other_tenant(maker):
    """A second practice in the database, so ownership is checked across two real tenants."""
    tenant_id = uuid.uuid4()

    async def create():
        async with maker() as s:
            s.add(Tenant(id=tenant_id, name="other practice"))
            await s.commit()

    asyncio.run(create())
    return tenant_id


def test_an_agency_sees_what_each_workspace_needs_without_opening_it(workspace, monkeypatch):
    """The overview card for one client: what it has, and what still stands between it and a
    working phone line — read through the same tenant-scoped token as everything else."""
    client, owner, _, _, maker = workspace
    monkeypatch.setattr(settings, "voice_number_assignments", {})

    empty = client.get("/api/v1/workspace/summary").json()
    assert empty["agents"] == [] and "No agent yet." in empty["attention"]

    agent = client.post("/api/v1/agents", json={"name": "Lena", "practice_type": "dental"}).json()
    summary = client.get("/api/v1/workspace/summary").json()

    assert [a["name"] for a in summary["agents"]] == ["Lena"]
    assert summary["agents"][0]["published"] is False and summary["agents"][0]["live"] is False
    assert "Lena has not been published yet." in summary["attention"]
    assert "No phone number claimed yet." in summary["attention"]
    assert summary["calls_7d"] == 0 and summary["callbacks_waiting"] == 0
    # The workspace fixture's practice holds a mapped Thevea connection.
    assert summary["calendars"] == 1
    assert agent["config"]["treatments"]

    owner.id = uuid.uuid4()
    assert client.get("/api/v1/workspace/summary").json()["agents"] == []


def test_activation_verifies_target_and_pause_preserves_ownership(workspace, monkeypatch):
    client, owner, connection, _, maker = workspace
    monkeypatch.setattr(service, "check_calendar", lambda *args: {"ok": True})

    async def connect(_session, tenant_id, number, *, webhook_url):
        assert tenant_id == owner.id
        assert webhook_url.endswith("/api/v1/telephony/incoming")

    monkeypatch.setattr(service.numbers, "connect", connect)
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


def test_a_number_moves_to_another_agent_only_once_its_agent_is_paused(workspace, monkeypatch):
    """One test number, several agents: a paused agent's number can be handed to another agent in
    the same practice. A number still answering calls is never taken silently."""
    client, owner, connection, _, maker = workspace
    monkeypatch.setattr(service, "check_calendar", lambda *args: {"ok": True})

    async def connect(_session, _tenant_id, _number, *, webhook_url):
        return None

    monkeypatch.setattr(service.numbers, "connect", connect)
    for key in ("smtp_host", "deepgram_api_key", "openai_api_key", "elevenlabs_api_key",
                "elevenlabs_voice_id"):
        monkeypatch.setattr(settings, key, "test-only")
    number = "+49" + str(uuid.uuid4().int)[:10]

    def published(name):
        agent = client.post("/api/v1/agents", json={"name": name}).json()
        path = "/api/v1/agents/" + agent["id"]
        config = {**complete_config(), "name": name}
        client.post(path + "/draft", json={"generation": 1, "config": config}).raise_for_status()
        instance = client.post(path + "/publish", json={"generation": 2}).json()["published_instance_id"]
        return path, {"instance_id": instance, "connection_id": str(connection), "phone_number": number}

    first, first_activation = published("Empfang")
    second, second_activation = published("Rezeption")
    assert client.post(first + "/activate", json=first_activation).status_code == 200

    refused = client.post(second + "/activate", json=second_activation)
    assert refused.status_code == 409
    assert "Empfang answers this number" in refused.json()["detail"]

    client.post(first + "/pause").raise_for_status()
    moved = client.post(second + "/activate", json=second_activation)
    assert moved.status_code == 200, moved.text
    assert moved.json()["channel"]["phone_number"] == number
    assert client.get(first).json()["channel"] is None


def test_a_published_agent_says_what_its_documents_said_when_it_was_published(workspace):
    """Documents belong to the workspace; publishing pins their content. Editing a document
    changes nothing a caller hears until the practice publishes again (ADR-0017)."""
    client, owner, _, _, maker = workspace

    doc = client.post(
        "/api/v1/knowledge", json={"title": "Parken", "content": "Parken im Hof."}
    ).json()
    assert [d["id"] for d in client.get("/api/v1/knowledge").json()["documents"]] == [doc["id"]]

    agent = client.post("/api/v1/agents").json()
    path = "/api/v1/agents/" + agent["id"]
    config = {**complete_config(), "knowledge_ids": [doc["id"]]}
    client.post(path + "/draft", json={"generation": 1, "config": config}).raise_for_status()
    first = client.post(path + "/publish", json={"generation": 2}).json()["published_instance_id"]

    edited = client.put(
        "/api/v1/knowledge/" + doc["id"], json={"title": "Parken", "content": "Parken nur vorne."}
    )
    assert edited.status_code == 200

    async def pinned(instance_id):
        async with maker() as s:
            instance = await repo.get_instance(s, uuid.UUID(instance_id), owner.id)
            return [d["content"] for d in instance.runtime_config["knowledge"]]

    assert asyncio.run(pinned(first)) == ["Parken im Hof."]
    second = client.post(path + "/publish", json={"generation": 2}).json()["published_instance_id"]
    assert second != first and asyncio.run(pinned(second)) == ["Parken nur vorne."]

    refused = client.delete("/api/v1/knowledge/" + doc["id"])
    assert refused.status_code == 409 and "still uses this document" in refused.json()["detail"]

    owner.id = uuid.uuid4()
    assert client.get("/api/v1/knowledge/" + doc["id"]).status_code == 404
    assert client.get("/api/v1/knowledge").json()["documents"] == []


def test_a_test_call_on_the_real_calendar_carries_its_mode_to_the_call(workspace, monkeypatch):
    """The Studio chose "read" on this practice's Thevea account: the socket must build that
    calendar and the booking session must know it may not write (ADR-0018)."""
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit

    from app.agents.runtime import open_voice_call
    from app.workloads.conversational.calendar import VOICE_CALENDARS

    client, owner, connection, foreign, maker = workspace
    for key in ("deepgram_api_key", "openai_api_key", "elevenlabs_api_key", "elevenlabs_voice_id"):
        monkeypatch.setattr(settings, key, "test-only")
    built = []
    system = VOICE_CALENDARS["thevea"]
    monkeypatch.setitem(
        VOICE_CALENDARS,
        "thevea",
        system.__class__(
            **{**system.__dict__, "build": lambda c, p: built.append(c.id) or SimpleNamespace(close=lambda: None)}
        ),
    )
    agent = client.post("/api/v1/agents").json()
    session = {"agent_id": agent["id"], "generation": 1, "connection_id": str(connection)}

    assert client.post(
        "/api/v1/conversational/sessions", json={**session, "calendar_mode": "write"}
    ).status_code == 422
    assert client.post(
        "/api/v1/conversational/sessions",
        json={**session, "calendar_mode": "read", "connection_id": str(foreign)},
    ).status_code == 404

    started = client.post("/api/v1/conversational/sessions", json={**session, "calendar_mode": "read"})
    assert started.status_code == 200, started.text
    token = parse_qs(urlsplit(started.json()["ws_url"]).query)["token"][0]

    call = asyncio.run(open_voice_call(token, sessionmaker=maker))

    assert call.calendar_mode == "read" and call.rehearsal is True
    assert built == [connection, connection]  # once to prove it at start, once for the call
    detail = client.get("/api/v1/conversations/" + started.json()["conversation_id"]).json()
    assert detail["metadata"]["calendar"] == "thevea"
    assert detail["metadata"]["calendar_mode"] == "read"


def test_a_practice_web_page_becomes_a_document_it_can_edit(workspace, monkeypatch):
    from app.api.v1 import knowledge

    client, *_ = workspace
    monkeypatch.setattr(
        knowledge.web_import,
        "fetch",
        lambda url: "<title>Preise | Praxis</title><main><p>Parken im Hof.</p></main>",
    )

    added = client.post("/api/v1/knowledge/url", json={"url": "https://praxis.example/preise"})

    assert added.status_code == 200, added.text
    assert added.json()["title"] == "Preise | Praxis" and added.json()["source"] == "url"
    content = client.get("/api/v1/knowledge/" + added.json()["id"]).json()["content"]
    assert content.startswith("Source: https://praxis.example/preise") and "Parken im Hof." in content


def test_an_agent_saved_before_a_field_existed_still_opens(workspace):
    """A draft stored before `knowledge_ids` existed has no key for it. Served raw, the Studio read
    `config.knowledge_ids.length` on it and the whole page crashed."""
    from sqlalchemy import update

    from app.db.models import StudioAgent

    client, owner, _, _, maker = workspace
    agent = client.post("/api/v1/agents").json()
    old_draft = {k: v for k, v in agent["config"].items()
                 if k not in ("knowledge_ids", "transfer_number", "transcript_retention_days")}

    async def store_old():
        async with maker() as s:
            await s.execute(
                update(StudioAgent).where(StudioAgent.id == uuid.UUID(agent["id"])).values(draft=old_draft)
            )
            await s.commit()

    asyncio.run(store_old())
    config = client.get("/api/v1/agents/" + agent["id"]).json()["config"]

    assert config["knowledge_ids"] == [] and config["transfer_number"] == ""
    assert config["transcript_retention_days"] == 30


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
