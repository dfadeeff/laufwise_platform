"""A deployed calendar import or availability mirror on a workspace schedule (ADR-0040 task 4),
against Postgres. Each occurrence starts one import job through the same worker the Run button
uses, over the days it is due for, and one run of an import goes at a time in a workspace,
whichever deployment or button starts it."""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import Principal, current_principal, current_tenant
from app.db import repo
from app.db.models import (
    AgentInstance,
    AgentSchedule,
    Connection,
    ImportJob,
    InstanceConnection,
    ScheduleOccurrence,
    ScheduleWorker,
    Template,
    Tenant,
)
from app.main import app
from app.schedules import targets, worker
from tests.test_schedule_worker import _sessionmaker, _yes
from tests.test_workflow_agents import _as, _db_reachable

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
client = TestClient(app)
UTC = timezone.utc
ORG, OTHER = "org_test_sched_rb", "org_test_sched_rb_other"
ALICE = Principal("user_alice", "org:admin")
# Monday 9 November 2026, 07:00 UTC = 08:00 in Berlin.
MON_0800 = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


async def _cleanup(sm) -> None:
    async with sm() as s:
        await s.execute(delete(ScheduleWorker))
        for org in (ORG, OTHER):
            tenant = (await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))).scalar_one_or_none()
            if tenant is None:
                continue
            await s.execute(delete(ScheduleOccurrence).where(ScheduleOccurrence.tenant_id == tenant.id))
            await s.execute(delete(AgentSchedule).where(AgentSchedule.tenant_id == tenant.id))
            await s.execute(delete(ImportJob).where(ImportJob.tenant_id == tenant.id))
            templates = select(AgentInstance.template_id).where(AgentInstance.tenant_id == tenant.id)
            ids = list((await s.execute(templates)).scalars())
            instances = select(AgentInstance.id).where(AgentInstance.tenant_id == tenant.id)
            await s.execute(delete(InstanceConnection).where(InstanceConnection.instance_id.in_(instances)))
            await s.execute(delete(Connection).where(Connection.tenant_id == tenant.id))
            await s.execute(delete(AgentInstance).where(AgentInstance.tenant_id == tenant.id))
            await s.execute(delete(Template).where(Template.id.in_(ids)))
        await s.commit()


async def _deployment(sm, org: str, *, name="calendar_import", status="deployed", template_id=None) -> dict:
    async with sm() as s:
        tenant = await repo.tenant_for_org(s, org, name=org)
        template = await s.get(Template, template_id) if template_id else None
        if template is None:
            template = Template(
                name=name, version=random.randint(100_000, 999_999), agent_class="workflow",
                status="published", contract={"steps": []}, parameters={},
            )
            s.add(template)
            await s.flush()
        instance = AgentInstance(
            tenant_id=tenant.id, template_id=template.id, template_version=template.version,
            param_values={}, status=status,
        )
        s.add(instance)
        await s.commit()
        return {"tenant": tenant.id, "instance": instance.id, "template": template.id}


@pytest.fixture
def ws(monkeypatch):
    sm = _sessionmaker()
    asyncio.run(_cleanup(sm))
    spawned: list[tuple] = []
    monkeypatch.setattr(targets, "_spawn", lambda *args: spawned.append(args))
    app.dependency_overrides[current_tenant] = _as(ORG, "praxis")
    app.dependency_overrides[current_principal] = lambda: ALICE
    yield {"sm": sm, "spawned": spawned}
    app.dependency_overrides.pop(current_tenant, None)
    app.dependency_overrides.pop(current_principal, None)
    asyncio.run(_cleanup(sm))


def _create(instance_id, **overrides):
    body = {
        "target": {"kind": "runbook", "id": str(instance_id)},
        "name": "Dreimal täglich",
        "rule": {"kind": "times", "days": [1, 2, 3, 4, 5], "at": ["08:00", "13:00", "18:00"]},
        "input": {"days": 7},
        **overrides,
    }
    return client.post("/api/v1/schedules", json=body)


def _due(sm, sid: str, when: datetime) -> None:
    async def go():
        async with sm() as s:
            schedule = await s.get(AgentSchedule, uuid.UUID(sid))
            schedule.next_run_at = when
            await s.commit()

    asyncio.run(go())


def _jobs(sm, instance_id) -> list[ImportJob]:
    async def go():
        async with sm() as s:
            return list((await s.execute(select(ImportJob).where(ImportJob.instance_id == instance_id))).scalars())

    return asyncio.run(go())


@db
def test_a_scheduled_import_starts_one_import_job_over_the_days_it_is_due_for(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    made = _create(dep["instance"])
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["target"] == {
        "kind": "runbook", "id": str(dep["instance"]), "name": "Calendar import", "key": "calendar_import",
    }
    assert body["input_schema"]["properties"]["days"]["type"] == "integer"
    _due(ws["sm"], body["id"], MON_0800)

    asyncio.run(worker.tick(ws["sm"], "node-a", now=MON_0800 + timedelta(seconds=5), authority=_yes))

    [job] = _jobs(ws["sm"], dep["instance"])
    # Queued by the occurrence, then taken by the same worker in the same tick.
    assert (job.status, job.trigger, job.locked_by, job.attempt) == ("running", "schedule", "node-a", 1)
    [occ] = client.get(f"/api/v1/schedules/{body['id']}/occurrences").json()
    assert (occ["status"], occ["run_ref"]) == ("started", f"import_job:{job.id}")
    # The days are counted from the day it is due, in Berlin: 9 November and six more.
    window = {"from": "2026-11-09", "to": "2026-11-15"}
    assert job.window == window
    assert ws["spawned"] == [(job.id, dep["instance"], dep["tenant"], window, "node-a")]


@db
def test_an_import_still_running_is_not_started_twice(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    sid = _create(dep["instance"]).json()["id"]

    _running(ws["sm"], dep)
    _due(ws["sm"], sid, MON_0800)
    asyncio.run(worker.tick(ws["sm"], "node-a", now=MON_0800 + timedelta(seconds=5), authority=_yes))

    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert occ["status"] == "skipped_busy" and "still running" in occ["error"]
    assert len(_jobs(ws["sm"], dep["instance"])) == 1 and ws["spawned"] == []


@pytest.mark.parametrize("days", [0, 71, "8", True])
@db
def test_the_days_it_covers_are_bounded(ws, days) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    assert _create(dep["instance"], input={"days": days}).status_code == 422
    assert _create(dep["instance"], input={"days": 7, "rooms": "1"}).status_code == 422


@db
def test_only_a_deployed_import_or_mirror_of_this_workspace_is_scheduled(ws) -> None:
    paused = asyncio.run(_deployment(ws["sm"], ORG, status="paused"))
    assert _create(paused["instance"]).status_code == 422
    voice = asyncio.run(_deployment(ws["sm"], ORG, name="voice_appointment"))
    assert _create(voice["instance"]).status_code == 422
    theirs = asyncio.run(_deployment(ws["sm"], OTHER))
    assert _create(theirs["instance"]).status_code == 404


@db
def test_a_stale_import_left_by_a_dead_worker_is_reclaimed_by_the_tick(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))

    old = _running(ws["sm"], dep, window=None, minutes_ago=60)
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [job] = _jobs(ws["sm"], dep["instance"])
    assert job.id == old and job.status == "interrupted"  # it has no window to run again on


WINDOW = {"from": "2026-11-09", "to": "2026-11-15"}


def _running(sm, dep, *, node="node-dead", window=WINDOW, attempt=1, minutes_ago=0):
    """An import another worker has taken and is running."""
    async def go():
        async with sm() as s:
            job = await repo.create_import_job(
                s, tenant_id=dep["tenant"], instance_id=dep["instance"], window=window, commit=False
            )
            job.status, job.locked_by, job.attempt = "running", node, attempt
            await s.flush()
            if minutes_ago:
                job.updated_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
            await s.commit()
            return job.id

    return asyncio.run(go())


@db
def test_two_deployments_of_one_import_never_run_at_once(ws) -> None:
    """A redeploy leaves the earlier deployment running; both write into the same thevea."""
    earlier = asyncio.run(_deployment(ws["sm"], ORG))
    later = asyncio.run(_deployment(ws["sm"], ORG, template_id=earlier["template"]))
    sid = _create(later["instance"]).json()["id"]
    _running(ws["sm"], earlier)

    _due(ws["sm"], sid, MON_0800)
    asyncio.run(worker.tick(ws["sm"], "node-a", now=MON_0800 + timedelta(seconds=5), authority=_yes))
    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert occ["status"] == "skipped_busy" and ws["spawned"] == []

    # The button on the later deployment does not start a second one either.
    res = client.post(f"/api/v1/instances/{later['instance']}/import")
    assert res.status_code == 409 and "another deployment" in res.text


@db
def test_a_stopping_worker_hands_its_unfinished_imports_to_the_next(ws) -> None:
    import threading

    dep = asyncio.run(_deployment(ws["sm"], ORG))

    job_id = _running(ws["sm"], dep, node="node-a")
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    targets.LIVE_IMPORTS.clear()
    targets.LIVE_IMPORTS[job_id] = stuck
    try:
        asyncio.run(worker.drain(ws["sm"], "node-a", budget=0.2))
    finally:
        release.set()
        targets.LIVE_IMPORTS.clear()
    [row] = _jobs(ws["sm"], dep["instance"])
    # Handed to the next worker, which runs it again: the contract skips what is already written.
    assert (row.status, row.locked_by, row.attempt) == ("queued", None, 1)
    asyncio.run(worker.tick(ws["sm"], "node-b", authority=_yes))
    [row] = _jobs(ws["sm"], dep["instance"])
    assert (row.status, row.locked_by, row.attempt) == ("running", "node-b", 2)
    assert ws["spawned"] == [(job_id, dep["instance"], dep["tenant"], WINDOW, "node-b")]

@db
def test_removing_a_connection_a_schedule_still_uses_says_so(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))

    async def bind():
        async with ws["sm"]() as s:
            connection = Connection(tenant_id=dep["tenant"], type="calendar", adapter="memory", config={})
            s.add(connection)
            await s.flush()
            s.add(InstanceConnection(instance_id=dep["instance"], role="source", connection_id=connection.id))
            await s.commit()
            return connection.id

    connection_id = asyncio.run(bind())
    sid = _create(dep["instance"]).json()["id"]

    async def reasons():
        async with ws["sm"]() as s:
            return await repo.connection_in_use(s, connection_id)

    assert 'The schedule "Dreimal täglich" runs a workflow with it.' in asyncio.run(reasons())
    client.post(f"/api/v1/schedules/{sid}/pause")
    assert not any("schedule" in r for r in asyncio.run(reasons()))


@db
def test_the_studio_can_tell_whether_the_worker_runs(ws) -> None:
    assert client.get("/api/v1/schedules/status").json()["running"] is False
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    assert client.get("/api/v1/schedules/status").json()["running"] is True


@db
def test_a_scheduled_import_shows_the_version_its_deployment_runs(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    made = _create(dep["instance"])
    assert made.status_code == 201, made.text
    version = asyncio.run(_template_version(ws["sm"], dep["template"]))
    assert made.json()["version"] == {"id": str(dep["instance"]), "number": version}
    listed = client.get("/api/v1/schedules").json()
    assert [s["version"]["number"] for s in listed] == [version]


async def _template_version(sm, template_id) -> int:
    async with sm() as s:
        return (await s.get(Template, template_id)).version


@db
def test_a_worker_that_died_hands_its_import_on_until_the_last_attempt(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    _running(ws["sm"], dep, attempt=1, minutes_ago=60)
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [row] = _jobs(ws["sm"], dep["instance"])
    assert (row.status, row.locked_by, row.attempt) == ("running", "node-a", 2)
    assert "started again" in row.error

    last = asyncio.run(_deployment(ws["sm"], OTHER))
    _running(ws["sm"], last, attempt=repo.MAX_IMPORT_ATTEMPTS, minutes_ago=60)
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [row] = _jobs(ws["sm"], last["instance"])
    assert row.status == "interrupted" and "not started again" in row.error


@db
def test_the_button_only_queues_the_import_and_the_worker_starts_it(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    started = client.post(f"/api/v1/instances/{dep['instance']}/import")
    assert started.status_code == 202 and started.json()["status"] == "queued"
    assert ws["spawned"] == []  # nothing runs in the API process
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [job] = _jobs(ws["sm"], dep["instance"])
    assert (job.status, job.trigger, job.locked_by) == ("running", "manual", "node-a")


@db
def test_a_worker_takes_no_more_imports_than_it_has_room_for(ws, monkeypatch) -> None:
    monkeypatch.setattr(targets, "MAX_LIVE_IMPORTS", 1)
    first = asyncio.run(_deployment(ws["sm"], ORG))
    second = asyncio.run(_deployment(ws["sm"], OTHER))
    for dep in (first, second):
        async def queue(dep=dep):
            async with ws["sm"]() as s:
                await repo.create_import_job(s, tenant_id=dep["tenant"], instance_id=dep["instance"], window=WINDOW)
        asyncio.run(queue())

    class Alive:
        def is_alive(self):
            return True

    monkeypatch.setattr(targets, "_spawn", lambda job_id, *a: targets.LIVE_IMPORTS.__setitem__(job_id, Alive()))
    targets.LIVE_IMPORTS.clear()
    try:
        asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
        statuses = sorted(j.status for dep in (first, second) for j in _jobs(ws["sm"], dep["instance"]))
    finally:
        targets.LIVE_IMPORTS.clear()
    assert statuses == ["queued", "running"]


@db
def test_a_run_whose_job_was_handed_on_writes_nothing(ws) -> None:
    from app.sync import jobs

    dep = asyncio.run(_deployment(ws["sm"], ORG))
    job_id = _running(ws["sm"], dep, node="node-b")  # node-a lost it to node-b
    asyncio.run(jobs.execute_import_job(job_id, dep["instance"], dep["tenant"], WINDOW, "node-a"))
    [row] = _jobs(ws["sm"], dep["instance"])
    assert (row.status, row.locked_by, row.total) == ("running", "node-b", 0)
