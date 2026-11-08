"""A doctolib sign-in runs in the schedule worker's browser, not in the API process, against
Postgres. The API queues it and serves its polls; the emailed code reaches the browser through the
row; every secret on the row is sealed, and wiped once the sign-in has its outcome."""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from app.api.v1 import connections as api
from app.config import settings
from app.connections import doctolib_login_jobs as jobs
from app.db import repo
from app.db.models import Connection, DoctolibLogin, Tenant
from tests.test_schedule_worker import _sessionmaker
from tests.test_workflow_agents import _db_reachable

db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
ORG, OTHER = "org_test_doctolib_login", "org_test_doctolib_login_other"
SESSION = {"_doctolib_session": "fresh", "pin_login": "c", "browser_state": "state"}


async def _cleanup(sm) -> None:
    async with sm() as s:
        for org in (ORG, OTHER):
            tenant = (await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))).scalar_one_or_none()
            if tenant is not None:
                await s.execute(delete(DoctolibLogin).where(DoctolibLogin.tenant_id == tenant.id))
                await s.execute(delete(Connection).where(Connection.tenant_id == tenant.id))
        await s.commit()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", Fernet.generate_key().decode())
    monkeypatch.setattr(jobs, "_CODE_POLL_S", 0.05)
    sm = _sessionmaker()
    asyncio.run(_cleanup(sm))

    async def tenants():
        async with sm() as s:
            a = await repo.tenant_for_org(s, ORG, name=ORG)
            b = await repo.tenant_for_org(s, OTHER, name=OTHER)
            await s.commit()
            return a, b

    mine, theirs = asyncio.run(tenants())
    jobs.LIVE_LOGINS.clear()
    yield SimpleNamespace(sm=sm, tenant=mine, other=theirs)
    for thread in jobs.LIVE_LOGINS.values():
        thread.join(5)
    jobs.LIVE_LOGINS.clear()
    asyncio.run(_cleanup(sm))


def _browser(monkeypatch, *, asks_for_code: bool):
    """A stand-in for the headless browser: it may wait on the code screen, then signs in."""
    seen: dict = {}
    monkeypatch.setattr(api, "_verify_credentials", _accept)

    def headless_login(username, password, get_code, *, pin_login="", browser_state="", pin=""):
        seen.update(username=username, password=password, pin=pin)
        if asks_for_code:
            seen["code"] = get_code()
            if seen["code"] is None:
                raise RuntimeError("no code")
        return dict(SESSION)

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    return seen


async def _accept(adapter, config, creds):
    return None


def _start(env, tenant=None, **kw):
    async def go():
        async with env.sm() as s:
            return await jobs.start_login(
                s, (tenant or env.tenant).id, "praxis@example.de", "secret-pw", "1,2",
                {"label": "Praxis"}, pin="4321", **kw,
            )

    return asyncio.run(go()).id


def _row(env, job_id) -> DoctolibLogin:
    async def go():
        async with env.sm() as s:
            return await s.get(DoctolibLogin, uuid.UUID(job_id))

    return asyncio.run(go())


def _poll(env, job_id, tenant=None):
    async def go():
        async with env.sm() as s:
            return await api.poll_doctolib_login(job_id, s, tenant or env.tenant)

    return asyncio.run(go())


def _code(env, job_id, code):
    async def go():
        async with env.sm() as s:
            return await api.submit_doctolib_code(job_id, SimpleNamespace(code=code), s, env.tenant)

    return asyncio.run(go())


def _until(env, job_id, status, timeout=5.0):
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        if _row(env, job_id).status == status:
            return
        threading.Event().wait(0.05)
    raise AssertionError(f"never reached {status}: {_row(env, job_id).status}")


@db
def test_the_worker_signs_in_with_the_emailed_code_and_the_api_makes_the_connection(env, monkeypatch):
    seen = _browser(monkeypatch, asks_for_code=True)
    job_id = _start(env)
    row = _row(env, job_id)
    # Queued for the worker; nothing secret is readable on the row.
    assert row.status == "queued" and "secret-pw" not in row.secrets_enc and "4321" not in row.secrets_enc
    assert _poll(env, job_id).status == "queued"

    assert asyncio.run(jobs.start_queued(env.sm, "node-a")) == 1
    _until(env, job_id, "awaiting_code")
    assert _code(env, job_id, "123456").status == "awaiting_code"
    _until(env, job_id, "done")
    assert seen == {"username": "praxis@example.de", "password": "secret-pw", "pin": "4321", "code": "123456"}

    status = _poll(env, job_id)
    assert status.status == "done" and status.connection_id
    row = _row(env, job_id)
    assert (row.secrets_enc, row.code_enc, row.result_enc) == (None, None, None)

    async def connections():
        async with env.sm() as s:
            return await repo.list_connections(s, env.tenant.id)

    [conn] = asyncio.run(connections())
    assert conn.id.hex == status.connection_id and conn.config["label"] == "Praxis"
    # A second poll does not make a second connection.
    assert _poll(env, job_id).connection_id == status.connection_id
    assert len(asyncio.run(connections())) == 1


@db
def test_another_workspace_cannot_see_or_feed_a_sign_in(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=False)
    job_id = _start(env)
    with pytest.raises(api.HTTPException) as exc:
        _poll(env, job_id, tenant=env.other)
    assert exc.value.status_code == 404


@db
def test_a_refused_sign_in_is_failed_and_wiped(env, monkeypatch):
    from app.providers.doctolib import DoctolibLoginRefused

    def headless_login(*args, **kwargs):
        raise DoctolibLoginRefused("doctolib said no", "password")

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    job_id = _start(env)
    asyncio.run(jobs.start_queued(env.sm, "node-a"))
    _until(env, job_id, "failed")
    row = _row(env, job_id)
    assert (row.refused, row.secrets_enc) == ("password", None)
    assert _poll(env, job_id).refused == "password"


@db
def test_a_sign_in_nobody_finished_is_failed_and_wiped_after_twenty_minutes(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=False)
    job_id = _start(env)  # no worker ever takes it

    async def sweep(now):
        async with env.sm() as s:
            await jobs.sweep(s, now)
            await s.commit()

    asyncio.run(sweep(datetime.now(timezone.utc) + timedelta(minutes=5)))
    assert _row(env, job_id).status == "queued"
    asyncio.run(sweep(datetime.now(timezone.utc) + timedelta(minutes=21)))
    row = _row(env, job_id)
    assert row.status == "failed" and row.secrets_enc is None and "start it again" in row.error


@db
def test_a_stopping_worker_fails_the_sign_in_its_browser_was_running(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=True)  # waits on the code screen; no code comes
    job_id = _start(env)
    asyncio.run(jobs.start_queued(env.sm, "node-a"))
    _until(env, job_id, "awaiting_code")
    asyncio.run(jobs.drain(env.sm, "node-a", budget=0.1))
    row = _row(env, job_id)
    assert row.status == "failed" and "restarted" in row.error and row.secrets_enc is None
    _until(env, job_id, "failed")  # its browser gives up, and writes nothing over the failure


@db
def test_a_worker_opens_no_more_browsers_than_it_has_room_for(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=True)
    monkeypatch.setattr(jobs, "MAX_LIVE_LOGINS", 1)
    first, second = _start(env), _start(env)
    assert asyncio.run(jobs.start_queued(env.sm, "node-a")) == 1
    assert [_row(env, first).status, _row(env, second).status].count("queued") == 1
    asyncio.run(jobs.drain(env.sm, "node-a", budget=0.1))


@db
def test_the_code_reaches_a_browser_running_inside_playwright(env, monkeypatch):
    """Playwright's sync API marks its thread as running an event loop; the worker's database
    steps must still work from inside it (a real browser waits for the code there)."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(api, "_verify_credentials", _accept)
    seen = {}

    def headless_login(username, password, get_code, **_):
        with sync_playwright():
            seen["code"] = get_code()
        return dict(SESSION)

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    job_id = _start(env)
    asyncio.run(jobs.start_queued(env.sm, "node-a"))
    _until(env, job_id, "awaiting_code", timeout=20)
    _code(env, job_id, "654321")
    _until(env, job_id, "done", timeout=20)
    assert seen["code"] == "654321"


@db
def test_a_sign_in_whose_worker_died_fails_once_it_stops_ticking(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=True)
    job_id = _start(env)
    asyncio.run(jobs.start_queued(env.sm, "node-gone"))  # never ticks: it is not a live worker
    _until(env, job_id, "awaiting_code")

    async def sweep():
        async with env.sm() as s:
            await jobs.sweep(s)
            await s.commit()

    asyncio.run(sweep())
    row = _row(env, job_id)
    assert row.status == "failed" and "restarted" in row.error and row.secrets_enc is None


@db
def test_a_code_nobody_is_waiting_for_is_refused(env, monkeypatch):
    _browser(monkeypatch, asks_for_code=False)
    job_id = _start(env)  # still queued
    with pytest.raises(api.HTTPException) as exc:
        _code(env, job_id, "123456")
    assert exc.value.status_code == 409 and _row(env, job_id).code_enc is None
