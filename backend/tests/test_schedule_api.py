"""A workspace manages its schedules through the API (ADR-0040 D8), against Postgres.

Administrators create, change, resume, delete and run them now; any member reads them and may
pause one, since stopping is always safe. Everything a schedule would be refused for when it
fires is refused when it is saved. Another workspace's schedule does not exist here."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import Principal, current_principal, current_tenant
from app.db.models import AgentSchedule, DefinitionVersion, ScheduleOccurrence
from app.kernel.definitions import checksum
from app.main import app
from app.schedules import service, worker
from tests.test_schedule_worker import THREE_A_DAY, _ORGS, _cleanup, _sessionmaker, _workspace, _yes
from tests.test_workflow_agents import _as, _db_reachable, _fixture

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
client = TestClient(app)
ALICE = Principal("user_alice", "org:admin")
CAROL = Principal("user_carol", "org:member")
UTC = timezone.utc


def _as_person(principal: Principal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _in(org: str) -> None:
    app.dependency_overrides[current_tenant] = _as(org, "demo")


@pytest.fixture
def ws():
    """Hanna's workspace: an agent of a schedulable workflow, Alice an administrator in it."""
    sm = _sessionmaker()
    asyncio.run(_cleanup(sm))
    workspace = asyncio.run(_workspace(sm, _ORGS[0]))
    workspace["sm"] = sm
    _in(_ORGS[0])
    _as_person(ALICE)
    yield workspace
    app.dependency_overrides.pop(current_tenant, None)
    app.dependency_overrides.pop(current_principal, None)
    asyncio.run(_cleanup(sm))


def _new(ws: dict, **overrides) -> dict:
    body = {
        "target": {"kind": "workflow", "id": str(ws["agent"])},
        "name": "Morgens",
        "rule": THREE_A_DAY,
        "input": _fixture("u1"),
        "enabled": True,
        **overrides,
    }
    return body


def _create(ws: dict, **overrides):
    return client.post("/api/v1/schedules", json=_new(ws, **overrides))


def _publish_another_version(ws: dict, number: int = 2) -> str:
    async def go():
        async with ws["sm"]() as s:
            first = await s.get(DefinitionVersion, ws["version"])
            content = dict(first.content)
            version = DefinitionVersion(
                definition_id=first.definition_id, version=number, status="published",
                content=content, checksum=checksum(content), published_at=datetime.now(UTC),
            )
            s.add(version)
            await s.commit()
            return str(version.id)

    return asyncio.run(go())


@db
def test_an_admin_schedules_an_agent_on_its_newest_published_version(ws) -> None:
    res = _create(ws)

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Morgens" and body["enabled"] is True and body["revision"] == 1
    assert body["target"] == {"kind": "workflow", "id": str(ws["agent"]), "name": "Umlagen"}
    assert body["version"] == {"id": str(ws["version"]), "number": 1}
    assert body["newer_version"] is None
    assert body["timezone"] == "Europe/Berlin"  # the default
    assert body["rule"] == THREE_A_DAY
    assert len(body["upcoming"]) == 5 and body["next_run_at"] == body["upcoming"][0]
    assert (body["created_by"], body["updated_by"]) == ("user_alice", "user_alice")
    assert body["last"] is None

    listed = client.get("/api/v1/schedules", params={"target_id": str(ws["agent"])}).json()
    assert [s["id"] for s in listed] == [body["id"]]


@db
def test_a_schedule_created_paused_has_no_next_time(ws) -> None:
    body = _create(ws, enabled=False).json()
    assert (body["enabled"], body["next_run_at"]) == (False, None)
    assert len(body["upcoming"]) == 5  # still previewed


@pytest.mark.parametrize(
    "overrides, status, says",
    [
        ({"rule": {"kind": "times", "at": ["08:15"]}}, 422, "grid"),
        ({"rule": {"kind": "every", "minutes": 15}}, 422, "30"),
        ({"timezone": "Mars/Olympus"}, 422, "timezone"),
        ({"input": {"disputed": "charge"}}, 422, "judgment"),
        ({"name": ""}, 422, ""),
        ({"target": {"kind": "voice", "id": "00000000-0000-0000-0000-000000000000"}}, 422, ""),
    ],
)
@db
def test_what_could_never_run_is_refused_when_saved(ws, overrides, status, says) -> None:
    res = _create(ws, **overrides)
    assert res.status_code == status, res.text
    assert says in res.text


@db
def test_a_draft_or_unschedulable_version_is_refused(ws) -> None:
    sm = ws["sm"]
    manual_only = asyncio.run(_workspace(sm, _ORGS[0], triggers=None))
    res = _create(manual_only)
    assert res.status_code == 422 and "cannot be scheduled" in res.text

    draft = asyncio.run(_workspace(sm, _ORGS[0], status="draft"))
    res = _create(draft, version_id=str(draft["version"]))
    assert res.status_code == 422 and "published" in res.text

    res = _create(draft)  # no published version to default to
    assert res.status_code == 422 and "published" in res.text


@db
def test_another_workspaces_agent_and_schedules_do_not_exist_here(ws) -> None:
    mine = _create(ws).json()
    theirs = asyncio.run(_workspace(ws["sm"], _ORGS[1]))

    assert _create(theirs).status_code == 404  # their agent, from my workspace
    res = _create(ws, version_id=str(theirs["version"]))  # my agent, their version
    assert res.status_code == 422

    _in(_ORGS[1])
    sid = mine["id"]
    assert client.get(f"/api/v1/schedules/{sid}").status_code == 404
    assert client.get("/api/v1/schedules").json() == []
    assert client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "name": "x"}).status_code == 404
    assert client.post(f"/api/v1/schedules/{sid}/pause").status_code == 404
    assert client.post(f"/api/v1/schedules/{sid}/run-now").status_code == 404
    assert client.delete(f"/api/v1/schedules/{sid}").status_code == 404
    assert client.get(f"/api/v1/schedules/{sid}/occurrences").status_code == 404
    assert client.get("/api/v1/schedules/not-a-uuid").status_code == 404


@db
def test_a_member_reads_and_pauses_but_only_an_admin_changes_or_resumes(ws) -> None:
    sid = _create(ws).json()["id"]
    _as_person(CAROL)

    assert client.get(f"/api/v1/schedules/{sid}").status_code == 200
    assert _create(ws).status_code == 403
    assert client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "name": "x"}).status_code == 403
    assert client.post(f"/api/v1/schedules/{sid}/run-now").status_code == 403
    assert client.delete(f"/api/v1/schedules/{sid}").status_code == 403

    paused = client.post(f"/api/v1/schedules/{sid}/pause")
    assert paused.status_code == 200
    assert (paused.json()["enabled"], paused.json()["updated_by"]) == (False, "user_carol")
    assert client.post(f"/api/v1/schedules/{sid}/resume").status_code == 403

    _as_person(ALICE)
    resumed = client.post(f"/api/v1/schedules/{sid}/resume").json()
    assert resumed["enabled"] is True and resumed["next_run_at"] is not None


@db
def test_a_change_needs_the_revision_it_was_made_on(ws) -> None:
    created = _create(ws).json()
    sid = created["id"]

    changed = client.patch(
        f"/api/v1/schedules/{sid}",
        json={"revision": 1, "rule": {"kind": "times", "at": ["07:30"]}, "name": "Früh"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["revision"], body["name"]) == (2, "Früh")
    assert body["rule"] == {"kind": "times", "at": ["07:30"], "days": [1, 2, 3, 4, 5, 6, 7]}
    assert body["next_run_at"] == body["upcoming"][0] != created["next_run_at"]
    berlin = ZoneInfo("Europe/Berlin")
    assert {datetime.fromisoformat(t).astimezone(berlin).strftime("%H:%M") for t in body["upcoming"]} == {"07:30"}

    stale = client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "name": "Spät"})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/schedules/{sid}").json()["name"] == "Früh"

    bad = client.patch(f"/api/v1/schedules/{sid}", json={"revision": 2, "rule": {"kind": "every", "minutes": 5}})
    assert bad.status_code == 422


@db
def test_a_newer_published_version_is_shown_and_taken_only_when_chosen(ws) -> None:
    sid = _create(ws).json()["id"]
    newer = _publish_another_version(ws)

    body = client.get(f"/api/v1/schedules/{sid}").json()
    assert body["version"]["number"] == 1  # not moved by the publish
    assert body["newer_version"] == {"id": newer, "number": 2}

    moved = client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "version_id": newer}).json()
    assert moved["version"] == {"id": newer, "number": 2} and moved["newer_version"] is None


@db
def test_deleting_keeps_the_history_and_cancels_what_was_queued(ws) -> None:
    sid = _create(ws).json()["id"]
    assert client.post(f"/api/v1/schedules/{sid}/run-now").status_code == 202

    assert client.delete(f"/api/v1/schedules/{sid}").status_code == 204
    assert client.get(f"/api/v1/schedules/{sid}").status_code == 404
    assert client.get("/api/v1/schedules").json() == []

    async def occurrences():
        async with ws["sm"]() as s:
            stmt = select(ScheduleOccurrence).where(ScheduleOccurrence.schedule_id == uuid.UUID(sid))
            return list((await s.execute(stmt)).scalars())

    [occ] = asyncio.run(occurrences())
    # Still there: the history of what it did.
    assert (occ.status, occ.error) == ("cancelled", "the schedule was deleted before it started")


@db
def test_a_workspace_has_a_limit_of_active_schedules(ws, monkeypatch) -> None:
    monkeypatch.setattr(service, "MAX_ACTIVE_PER_WORKSPACE", 1)
    first = _create(ws).json()

    assert _create(ws).status_code == 409
    assert _create(ws, enabled=False).status_code == 201  # a paused one does not count
    paused = _create(ws, enabled=False).json()
    assert client.post(f"/api/v1/schedules/{paused['id']}/resume").status_code == 409
    client.post(f"/api/v1/schedules/{first['id']}/pause")
    assert client.post(f"/api/v1/schedules/{paused['id']}/resume").status_code == 200


@db
def test_run_now_runs_on_the_next_tick_even_when_paused_and_shows_in_the_history(ws) -> None:
    sid = _create(ws, enabled=False).json()["id"]

    queued = client.post(f"/api/v1/schedules/{sid}/run-now")
    assert queued.status_code == 202
    assert (queued.json()["origin"], queued.json()["status"]) == ("run_now", "queued")

    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))

    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert (occ["origin"], occ["status"], occ["outcome"]) == ("run_now", "succeeded", "ready")
    assert occ["run_ref"].startswith("case:")
    last = client.get(f"/api/v1/schedules/{sid}").json()["last"]
    assert last["status"] == "succeeded" and last["run_ref"] == occ["run_ref"]


@db
def test_the_studio_previews_a_rule_before_it_is_saved(ws) -> None:
    res = client.post("/api/v1/schedules/preview", json={"rule": THREE_A_DAY, "count": 3})
    assert res.status_code == 200
    assert len(res.json()["times"]) == 3
    assert client.post("/api/v1/schedules/preview", json={"rule": {"kind": "times", "at": ["9"]}}).status_code == 422
    assert client.post("/api/v1/schedules/preview", json={"rule": THREE_A_DAY, "count": 50}).status_code == 422



def _due_now(ws: dict, sid: str) -> datetime:
    """Make the schedule's next time one that has just come and not been queued yet."""
    async def go():
        async with ws["sm"]() as s:
            schedule = await s.get(AgentSchedule, uuid.UUID(sid))
            schedule.next_run_at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=5)
            await s.commit()
            return schedule.next_run_at

    return asyncio.run(go())


@db
def test_a_rename_never_skips_a_time_that_is_due(ws) -> None:
    sid = _create(ws).json()["id"]
    due = _due_now(ws, sid)

    renamed = client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "name": "Neu"}).json()
    assert datetime.fromisoformat(renamed["next_run_at"]) == due
    resumed = client.post(f"/api/v1/schedules/{sid}/resume").json()  # already on: changes nothing
    assert datetime.fromisoformat(resumed["next_run_at"]) == due

    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert datetime.fromisoformat(occ["scheduled_for"]) == due and occ["status"] == "succeeded"


@db
def test_a_change_cannot_bring_in_another_workspaces_version_or_agent(ws) -> None:
    sid = _create(ws).json()["id"]
    theirs = asyncio.run(_workspace(ws["sm"], _ORGS[1]))

    res = client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "version_id": str(theirs["version"])})
    assert res.status_code == 422
    moved = client.patch(
        f"/api/v1/schedules/{sid}",
        json={"revision": 1, "name": "x", "target": {"kind": "workflow", "id": str(theirs["agent"])}},
    ).json()
    assert moved["target"]["id"] == str(ws["agent"])  # a schedule is never moved to another agent


@db
def test_an_oversized_input_or_a_blank_name_is_refused(ws) -> None:
    assert _create(ws, input={"blob": "x" * 300_000}).status_code == 422
    assert _create(ws, name="   ").status_code == 422
    sid = _create(ws).json()["id"]
    assert client.patch(f"/api/v1/schedules/{sid}", json={"revision": 1, "name": "  "}).status_code == 422


@db
def test_a_members_pause_cancels_a_queued_run_now(ws) -> None:
    sid = _create(ws).json()["id"]
    client.post(f"/api/v1/schedules/{sid}/run-now")
    _as_person(CAROL)

    client.post(f"/api/v1/schedules/{sid}/pause")

    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert (occ["origin"], occ["status"], occ["error"]) == ("run_now", "cancelled", "paused before it started")
