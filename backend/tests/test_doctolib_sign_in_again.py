""""Sign in again": one click logs a doctolib connection in with what it already stores.

Reconnecting used to mean typing the email, password and PIN again and then binding the new
connection everywhere. Signing in again refreshes the SAME connection, so nothing has to be
re-bound, and a detail doctolib turns down comes back as which one to ask for (`refused`).
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import connections as api
from app.connections import doctolib_login_jobs as jobs
from app.providers.doctolib import DoctolibLoginRefused
from app.schemas.connection import DoctolibSignInAgain

TENANT = SimpleNamespace(id=uuid.uuid4())
CID = uuid.uuid4()
STORED = {"username": "praxis@example.de", "password": "old-pw", "pin": "1111",
          "pin_login": "c", "browser_state": "state", "session_cookie": "dead"}


def _conn(**over):
    base = dict(id=CID, adapter="doctolib", removed_at=None, tokens_enc=json.dumps(STORED),
                config={"agenda_ids": "1,2", "label": "Praxis", "device_trusted": True})
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def started(monkeypatch):
    seen: dict = {}

    async def get_connection(session, cid, tenant_id):
        return seen.get("conn", _conn()) if cid == CID else None

    def start_login(tenant_id, username, password, agenda_ids, config=None, **trust):
        seen.update(username=username, password=password, agenda_ids=agenda_ids, **trust)
        return jobs.LoginJob(id="j", tenant_id=tenant_id, username=username, agenda_ids=agenda_ids,
                             refresh_connection_id=trust.get("refresh_connection_id"))

    monkeypatch.setattr(api.repo, "get_connection", get_connection)
    monkeypatch.setattr(api.crypto, "decrypt", lambda blob: blob)
    monkeypatch.setattr(api.crypto, "encrypt", lambda text: text)
    monkeypatch.setattr(api.doctolib_login_jobs, "start_login", start_login)
    return seen


def _sign_in(req=None, cid=CID):
    return asyncio.run(api.sign_in_again(str(cid), req or DoctolibSignInAgain(), None, TENANT))


def test_one_click_uses_what_the_connection_stores(started):
    _sign_in()
    assert started["username"] == "praxis@example.de"
    assert started["password"] == "old-pw" and started["pin"] == "1111"
    assert started["browser_state"] == "state" and started["agenda_ids"] == "1,2"
    assert started["refresh_connection_id"] == CID.hex


def test_new_details_replace_only_what_was_typed(started):
    _sign_in(DoctolibSignInAgain(pin="2222"))
    assert started["password"] == "old-pw" and started["pin"] == "2222"
    changed = STORED["password"] + "-changed"
    _sign_in(DoctolibSignInAgain(password=changed))
    assert started["password"] == changed and started["pin"] == "1111"


@pytest.mark.parametrize("conn", [None, "removed", "other"])
def test_only_a_live_doctolib_connection_of_this_tenant(started, conn):
    if conn == "removed":
        started["conn"] = _conn(removed_at="2026-11-01")
    elif conn == "other":
        started["conn"] = _conn(adapter="thevea")
    with pytest.raises(HTTPException) as exc:
        _sign_in(cid=uuid.uuid4() if conn is None else CID)
    assert exc.value.status_code == 404


def test_without_a_saved_password_it_asks_for_one(started):
    started["conn"] = _conn(tokens_enc=json.dumps({"username": "praxis@example.de"}))
    with pytest.raises(HTTPException) as exc:
        _sign_in()
    assert exc.value.status_code == 409


def test_a_finished_sign_in_refreshes_the_same_connection(monkeypatch):
    updated: dict = {}

    async def verify(adapter, config, creds):
        return None

    async def update_connection_login(session, cid, tenant_id, *, tokens_enc, config):
        updated.update(cid=cid, creds=json.loads(tokens_enc), config=config)
        return _conn()

    async def create_connection(session, **kwargs):
        raise AssertionError("signing in again must not create a second connection")

    async def get_connection(session, cid, tenant_id):
        return _conn()

    monkeypatch.setattr(api, "_verify_credentials", verify)
    monkeypatch.setattr(api.repo, "get_connection", get_connection)
    monkeypatch.setattr(api.repo, "update_connection_login", update_connection_login)
    monkeypatch.setattr(api.repo, "create_connection", create_connection)
    monkeypatch.setattr(api.crypto, "encrypt", lambda text: text)
    job = jobs.LoginJob(id="j", tenant_id=TENANT.id.hex, username="praxis@example.de",
                        agenda_ids="1,2", status="done", password=STORED["password"], pin="2222",
                        refresh_connection_id=CID.hex)
    job.result = {"_doctolib_session": "fresh", "pin_login": "c2", "browser_state": "state2"}

    asyncio.run(api._finalize_doctolib_login(None, TENANT, job))

    assert updated["cid"] == CID and job.connection_id == CID.hex
    assert updated["creds"]["session_cookie"] == "fresh" and updated["creds"]["pin"] == "2222"
    assert updated["config"]["label"] == "Praxis" and updated["config"]["device_trusted"] is True
    assert updated["config"]["signed_in_at"]


@pytest.mark.parametrize("detail", ["password", "pin"])
def test_a_refused_detail_says_which_one(monkeypatch, detail):
    done = threading.Event()

    def headless_login(*args, **kwargs):
        done.set()
        raise DoctolibLoginRefused("doctolib said no", detail)

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    job = jobs.start_login("t", "u", "p", "")
    assert done.wait(5)
    for _ in range(50):
        if job.status == "failed":
            break
        threading.Event().wait(0.05)
    assert job.status == "failed" and job.refused == detail
    assert api._login_status(job).refused == detail
