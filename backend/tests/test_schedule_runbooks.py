"""A deployed calendar import or availability mirror on a workspace schedule (ADR-0040 task 4),
against Postgres. Each occurrence starts one import job through the same worker the Run button
uses, over the days it is due for; a deployment is never on two clocks; and the old clock's
setting moves onto two schedules only while the platform's worker is running."""

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
    ImportJob,
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
            await s.execute(delete(AgentInstance).where(AgentInstance.tenant_id == tenant.id))
            await s.execute(delete(Template).where(Template.id.in_(ids)))
        await s.commit()


async def _deployment(sm, org: str, *, name="calendar_import", status="deployed", old_clock=None, template_id=None) -> dict:
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
            param_values={}, status=status, schedule=old_clock,
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
    assert (job.status, job.trigger) == ("running", "schedule")
    [occ] = client.get(f"/api/v1/schedules/{body['id']}/occurrences").json()
    assert (occ["status"], occ["run_ref"]) == ("started", f"import_job:{job.id}")
    # The days are counted from the day it is due, in Berlin: 9 November and six more.
    assert ws["spawned"] == [(job.id, dep["instance"], dep["tenant"], {"from": "2026-11-09", "to": "2026-11-15"})]


@db
def test_an_import_still_running_is_not_started_twice(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    sid = _create(dep["instance"]).json()["id"]

    async def running():
        async with ws["sm"]() as s:
            await repo.create_import_job(s, tenant_id=dep["tenant"], instance_id=dep["instance"])

    asyncio.run(running())
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
def test_a_deployment_on_the_old_clock_is_never_on_both(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG, old_clock="mirror"))

    res = _create(dep["instance"])
    assert res.status_code == 422 and "old clock" in res.text
    status = client.get(f"/api/v1/schedules/targets/runbook/{dep['instance']}").json()
    assert status["schedulable"] is False and "old clock" in status["reason"]
    assert status["old_clock"] is True


@db
def test_the_old_clock_moves_only_while_the_worker_runs_and_then_in_one_step(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG, old_clock="mirror"))
    body = {"instance_id": str(dep["instance"])}

    assert client.get("/api/v1/schedules/status").json()["running"] is False
    refused = client.post("/api/v1/schedules/move-off-old-clock", json=body)
    assert refused.status_code == 409 and "not running" in refused.text

    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))  # the worker is up: it ticked
    assert client.get("/api/v1/schedules/status").json()["running"] is True

    moved = client.post("/api/v1/schedules/move-off-old-clock", json=body)
    assert moved.status_code == 201, moved.text
    rules = sorted((s["name"], s["rule"]["kind"], s["input"]["days"]) for s in moved.json())
    assert rules == [("Next 8 days, every 30 minutes", "every", 8), ("Whole booking horizon, nightly", "times", 70)]

    async def instance():
        async with ws["sm"]() as s:
            return await s.get(AgentInstance, dep["instance"])

    assert asyncio.run(instance()).schedule is None  # off the old clock, in the same commit
    again = client.post("/api/v1/schedules/move-off-old-clock", json=body)
    assert again.status_code == 422  # not on the old clock any more


@db
def test_a_stale_import_left_by_a_dead_worker_is_reclaimed_by_the_tick(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))

    async def orphan():
        async with ws["sm"]() as s:
            job = await repo.create_import_job(s, tenant_id=dep["tenant"], instance_id=dep["instance"])
            job.updated_at = datetime.now(UTC) - timedelta(hours=1)
            await s.commit()

    asyncio.run(orphan())
    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    [job] = _jobs(ws["sm"], dep["instance"])
    assert job.status == "interrupted"


@db
def test_the_old_toggle_cannot_put_a_scheduled_deployment_on_a_second_clock(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    sid = _create(dep["instance"]).json()["id"]

    armed = client.put(f"/api/v1/instances/{dep['instance']}/schedule", json={"schedule": "mirror"})
    assert armed.status_code == 409 and "new scheduler" in armed.text

    client.post(f"/api/v1/schedules/{sid}/pause")
    assert client.put(f"/api/v1/instances/{dep['instance']}/schedule", json={"schedule": "mirror"}).status_code == 200
    resumed = client.post(f"/api/v1/schedules/{sid}/resume")
    assert resumed.status_code == 422 and "old clock" in resumed.text


def _running(sm, dep) -> None:
    async def go():
        async with sm() as s:
            await repo.create_import_job(s, tenant_id=dep["tenant"], instance_id=dep["instance"])

    asyncio.run(go())


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
def test_any_deployment_on_the_old_clock_keeps_the_others_off_the_new_one(ws) -> None:
    armed = asyncio.run(_deployment(ws["sm"], ORG, old_clock="mirror"))
    other = asyncio.run(_deployment(ws["sm"], ORG, template_id=armed["template"]))
    res = _create(other["instance"])
    assert res.status_code == 422 and "old clock" in res.text

    async def disarm():
        async with ws["sm"]() as s:
            (await s.get(AgentInstance, armed["instance"])).schedule = None
            await s.commit()

    asyncio.run(disarm())
    assert _create(other["instance"]).status_code == 201
    # ...and now the old toggle cannot arm the first deployment again.
    rearm = client.put(f"/api/v1/instances/{armed['instance']}/schedule", json={"schedule": "mirror"})
    assert rearm.status_code == 409


@db
def test_a_deployment_rearmed_just_before_its_time_fails_that_run_and_starts_nothing(ws) -> None:
    dep = asyncio.run(_deployment(ws["sm"], ORG))
    sid = _create(dep["instance"]).json()["id"]

    async def rearm():  # behind the API's back, as an old row might be
        async with ws["sm"]() as s:
            (await s.get(AgentInstance, dep["instance"])).schedule = "mirror"
            await s.commit()

    asyncio.run(rearm())
    _due(ws["sm"], sid, MON_0800)
    asyncio.run(worker.tick(ws["sm"], "node-a", now=MON_0800 + timedelta(seconds=5), authority=_yes))
    [occ] = client.get(f"/api/v1/schedules/{sid}/occurrences").json()
    assert occ["status"] == "failed" and "old clock" in occ["error"]
    assert ws["spawned"] == [] and _jobs(ws["sm"], dep["instance"]) == []


@db
def test_moving_off_the_old_clock_is_an_admins_and_only_for_this_workspace(ws, monkeypatch) -> None:
    from app.schedules import service

    asyncio.run(worker.tick(ws["sm"], "node-a", authority=_yes))
    theirs = asyncio.run(_deployment(ws["sm"], OTHER, old_clock="mirror"))
    assert client.post("/api/v1/schedules/move-off-old-clock", json={"instance_id": str(theirs["instance"])}).status_code == 404

    mine = asyncio.run(_deployment(ws["sm"], ORG, old_clock="mirror"))
    body = {"instance_id": str(mine["instance"])}
    app.dependency_overrides[current_principal] = lambda: Principal("user_carol", "org:member")
    assert client.post("/api/v1/schedules/move-off-old-clock", json=body).status_code == 403
    app.dependency_overrides[current_principal] = lambda: ALICE

    monkeypatch.setattr(service, "MAX_ACTIVE_PER_WORKSPACE", 1)  # room for one of the two
    assert client.post("/api/v1/schedules/move-off-old-clock", json=body).status_code == 409

    async def still_armed():
        async with ws["sm"]() as s:
            return (await s.get(AgentInstance, mine["instance"])).schedule

    assert asyncio.run(still_armed()) == "mirror"  # nothing half-moved
    assert client.get("/api/v1/schedules").json() == []


@db
def test_a_stopping_worker_marks_the_imports_it_could_not_finish(ws) -> None:
    import threading

    dep = asyncio.run(_deployment(ws["sm"], ORG))

    async def job():
        async with ws["sm"]() as s:
            return (await repo.create_import_job(s, tenant_id=dep["tenant"], instance_id=dep["instance"])).id

    job_id = asyncio.run(job())
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    targets.LIVE_IMPORTS.clear()
    targets.LIVE_IMPORTS[job_id] = stuck
    try:
        asyncio.run(worker.drain(ws["sm"], budget=0.2))
    finally:
        release.set()
        targets.LIVE_IMPORTS.clear()
    [row] = _jobs(ws["sm"], dep["instance"])
    assert row.status == "interrupted"
