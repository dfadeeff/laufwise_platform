"""A reconnect must not make doctolib e-mail a code while it still trusts the server.

doctolib asks for its e-mailed code only on a device it does not know; its `pin_login` cookie is how
it knows one. Every Studio reconnect used to log in WITHOUT that cookie, as a brand-new device, so
the practice was asked for a code each time — even though an earlier connection held a valid one.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.v1 import connections as api
from app.connections import doctolib_login_jobs as jobs
from app.schemas.connection import ConnectionSummary


def _conn(adapter="doctolib", **creds):
    return SimpleNamespace(adapter=adapter, tokens_enc=json.dumps(creds) if creds else None)


def _trust(monkeypatch, rows, username="praxis@example.de"):
    async def list_connections(session, tenant_id):
        return rows

    monkeypatch.setattr(api.repo, "list_connections", list_connections)
    monkeypatch.setattr(api.crypto, "decrypt", lambda blob: blob)
    return asyncio.run(api._known_device_trust(None, SimpleNamespace(id="t"), username))


def test_the_newest_trust_of_the_same_account_is_reused(monkeypatch):
    rows = [
        _conn(username="Praxis@Example.de ", pin_login="newest"),  # newest first
        _conn(username="praxis@example.de", pin_login="older"),
    ]
    assert _trust(monkeypatch, rows) == "newest"


def test_another_accounts_trust_or_system_is_never_presented(monkeypatch):
    rows = [
        _conn(username="someone@else.de", pin_login="theirs"),
        _conn(adapter="thevea", username="praxis@example.de", pin_login="not-doctolib"),
        _conn(username="praxis@example.de", pin_login=""),
    ]
    assert _trust(monkeypatch, rows) == ""


def test_an_unreadable_connection_is_skipped_not_fatal(monkeypatch):
    rows = [SimpleNamespace(adapter="doctolib", tokens_enc="{broken"), _conn(username="praxis@example.de", pin_login="ok")]
    assert _trust(monkeypatch, rows) == "ok"


def test_the_login_presents_the_trust_it_was_given(monkeypatch):
    seen = {}
    done = threading.Event()

    def headless_login(username, password, get_code, *, pin_login=""):
        seen["pin_login"] = pin_login
        done.set()
        return {"_doctolib_session": "s", "pin_login": pin_login}

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    job = jobs.start_login("t", "u", "p", "", pin_login="trusted")
    assert done.wait(5)
    assert seen["pin_login"] == "trusted"
    assert job.result == {"_doctolib_session": "s", "pin_login": "trusted"}


def _finalize(monkeypatch, pin_login):
    created = {}

    async def verify(adapter, config, creds):
        return None

    async def create_connection(session, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(id="c1")

    monkeypatch.setattr(api, "_verify_credentials", verify)
    monkeypatch.setattr(api.repo, "create_connection", create_connection)
    monkeypatch.setattr(api.crypto, "encrypt", lambda text: text)
    job = jobs.LoginJob(id="j", tenant_id="t", username="u", agenda_ids="", status="done")
    job.result = {"_doctolib_session": "s", "pin_login": pin_login}
    asyncio.run(api._finalize_doctolib_login(None, SimpleNamespace(id="t"), job))
    return created["config"]


def test_a_connection_records_whether_doctolib_remembered_the_server(monkeypatch):
    assert _finalize(monkeypatch, "cookie")["device_trusted"] is True
    assert _finalize(monkeypatch, "")["device_trusted"] is False


def test_the_studio_sees_it_for_doctolib_only():
    def row(adapter, config):
        return SimpleNamespace(
            adapter=adapter, config=config, type="calendar", id="x",
            created_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )

    assert ConnectionSummary.of(row("doctolib", {"device_trusted": False})).device_trusted is False
    assert ConnectionSummary.of(row("doctolib", {})).device_trusted is None
    assert ConnectionSummary.of(row("thevea", {"device_trusted": True})).device_trusted is None
