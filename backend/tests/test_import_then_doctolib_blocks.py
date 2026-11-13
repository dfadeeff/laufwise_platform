"""A calendar import is followed by the doctolib block mirror over the same days (ADR-0044 D7),
against Postgres: from the Run button and from a schedule alike, because both run through the
same import job."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.db import repo
from app.db.models import ImportJob
from app.sync import jobs
from tests.test_schedule_runbooks import ORG, _cleanup, _deployment
from tests.test_schedule_worker import _sessionmaker
from tests.test_workflow_agents import _db_reachable

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
WINDOW = {"from": "2026-11-16", "to": "2026-11-23"}
NODE = "node-test"


async def _noop(session, runtime, instance, window, on_progress=None):
    return None


async def _finished_import(sm, instance_id, tenant_id) -> ImportJob:
    """Run one import job to completion through the worker's own entry point."""
    async with sm() as s:
        job = await repo.create_import_job(s, tenant_id=tenant_id, instance_id=instance_id, window=WINDOW)
        job.status, job.locked_by = "running", NODE
        await s.commit()
    await jobs.execute_import_job(job.id, instance_id, tenant_id, WINDOW, NODE)
    async with sm() as s:
        return await s.get(ImportJob, job.id)


async def _queued(sm, instance_id) -> list[ImportJob]:
    async with sm() as s:
        rows = await s.execute(select(ImportJob).where(ImportJob.instance_id == instance_id))
        return list(rows.scalars())


@pytest.fixture
def sm(monkeypatch):
    monkeypatch.setitem(jobs._ORCHESTRATORS, "calendar_import", _noop)
    monkeypatch.setitem(jobs._ORCHESTRATORS, "doctolib_block_mirror", _noop)
    sm = _sessionmaker()
    asyncio.run(_cleanup(sm))
    yield sm
    asyncio.run(_cleanup(sm))


@db
def test_a_finished_import_queues_the_block_mirror_for_the_same_days(sm):
    async def scenario():
        imp = await _deployment(sm, ORG, name="calendar_import")
        mirror = await _deployment(sm, ORG, name="doctolib_block_mirror")

        done = await _finished_import(sm, imp["instance"], imp["tenant"])

        [queued] = await _queued(sm, mirror["instance"])
        assert (queued.status, queued.trigger, queued.window) == ("queued", "chained", WINDOW)
        assert done.status == "completed"
        assert done.followed_by["job_id"] == queued.id.hex
        assert done.followed_by["instance_id"] == mirror["instance"].hex

    asyncio.run(scenario())


@db
def test_a_mirror_already_on_its_way_is_the_one_that_follows(sm):
    async def scenario():
        imp = await _deployment(sm, ORG, name="calendar_import")
        mirror = await _deployment(sm, ORG, name="doctolib_block_mirror")
        async with sm() as s:
            waiting = await repo.create_import_job(
                s, tenant_id=mirror["tenant"], instance_id=mirror["instance"], window=WINDOW
            )

        done = await _finished_import(sm, imp["instance"], imp["tenant"])

        assert [j.id for j in await _queued(sm, mirror["instance"])] == [waiting.id]
        assert done.followed_by["job_id"] == waiting.id.hex

    asyncio.run(scenario())


@db
def test_without_a_deployed_mirror_an_import_is_followed_by_nothing(sm):
    async def scenario():
        imp = await _deployment(sm, ORG, name="calendar_import")
        await _deployment(sm, ORG, name="doctolib_block_mirror", status="paused")

        done = await _finished_import(sm, imp["instance"], imp["tenant"])

        assert (done.status, done.followed_by) == ("completed", None)

    asyncio.run(scenario())


@db
def test_the_mirror_itself_is_followed_by_nothing(sm):
    async def scenario():
        mirror = await _deployment(sm, ORG, name="doctolib_block_mirror")

        done = await _finished_import(sm, mirror["instance"], mirror["tenant"])

        assert done.followed_by is None
        assert len(await _queued(sm, mirror["instance"])) == 1  # only the run itself

    asyncio.run(scenario())
