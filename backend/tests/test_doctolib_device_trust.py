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
        _conn(username="Praxis@Example.de ", pin_login="newest", browser_state="state-new"),
        _conn(username="praxis@example.de", pin_login="older", browser_state="state-old"),
    ]
    assert _trust(monkeypatch, rows) == {"pin_login": "newest", "browser_state": "state-new", "pin": ""}


def test_a_connection_made_before_the_browser_state_was_kept_still_lends_its_cookie(monkeypatch):
    rows = [_conn(username="praxis@example.de", pin_login="only-cookie")]
    assert _trust(monkeypatch, rows) == {"pin_login": "only-cookie", "browser_state": "", "pin": ""}


def test_another_accounts_trust_or_system_is_never_presented(monkeypatch):
    rows = [
        _conn(username="someone@else.de", pin_login="theirs"),
        _conn(adapter="thevea", username="praxis@example.de", pin_login="not-doctolib"),
        _conn(username="praxis@example.de", pin_login=""),
    ]
    assert _trust(monkeypatch, rows) == {"pin_login": "", "browser_state": "", "pin": ""}


def test_an_unreadable_connection_is_skipped_not_fatal(monkeypatch):
    rows = [SimpleNamespace(adapter="doctolib", tokens_enc="{broken"), _conn(username="praxis@example.de", pin_login="ok")]
    assert _trust(monkeypatch, rows)["pin_login"] == "ok"


def test_the_login_presents_the_trust_it_was_given(monkeypatch):
    seen = {}
    done = threading.Event()

    def headless_login(username, password, get_code, *, pin_login="", browser_state="", pin=""):
        seen.update(pin_login=pin_login, browser_state=browser_state)
        done.set()
        return {"_doctolib_session": "s", "pin_login": pin_login, "browser_state": browser_state}

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    jobs.run_login(
        jobs.LoginJob(id="j", tenant_id="t", username="u", agenda_ids="", password="p",
                      pin_login="trusted", browser_state="state"),
        lambda: None,
    )
    assert done.is_set()
    assert seen == {"pin_login": "trusted", "browser_state": "state"}


def _finalize(monkeypatch, pin_login, browser_state=""):
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
    job.result = {"_doctolib_session": "s", "pin_login": pin_login, "browser_state": browser_state}
    asyncio.run(api._finalize_doctolib_login(None, SimpleNamespace(id="t"), job))
    return created


def test_a_connection_keeps_the_whole_browser_state(monkeypatch):
    created = _finalize(monkeypatch, "cookie", "state")
    assert json.loads(created["tokens_enc"])["browser_state"] == "state"
    assert created["config"]["device_trusted"] is True


def test_the_pin_login_cookie_alone_does_not_count_as_trusted(monkeypatch):
    # It only skips the e-mail field; doctolib still e-mailed a code with it alone.
    assert _finalize(monkeypatch, "cookie")["config"]["device_trusted"] is False


def test_a_relogin_during_an_import_presents_the_kept_browser_state(monkeypatch):
    from app.connections import resolve

    seen = {}

    def headless_login(username, password, get_code, *, pin_login="", browser_state="", pin=""):
        seen.update(pin_login=pin_login, browser_state=browser_state, code=get_code())
        return {"_doctolib_session": "fresh"}

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    creds = {"username": "u", "password": "p", "pin_login": "c", "browser_state": "state"}
    relogin = resolve._doctolib_relogin_opts(SimpleNamespace(id="conn-relogin"), creds)["relogin"]
    assert relogin() == "fresh"
    assert seen == {"pin_login": "c", "browser_state": "state", "code": None}


def test_only_doctolib_cookies_and_storage_are_kept():
    from app.providers.doctolib_login import _doctolib_state

    state = {
        "cookies": [{"name": "a", "domain": ".doctolib.de"}, {"name": "b", "domain": "auth.doctolib.de"},
                    {"name": "x", "domain": ".friendlycaptcha.com"}],
        "origins": [{"origin": "https://pro.doctolib.de", "localStorage": []},
                    {"origin": "https://cdn.example.com", "localStorage": []}],
    }
    kept = json.loads(_doctolib_state(state))
    assert [c["name"] for c in kept["cookies"]] == ["a", "b"]
    assert [o["origin"] for o in kept["origins"]] == ["https://pro.doctolib.de"]


def test_the_studio_sees_it_for_doctolib_only():
    def row(adapter, config):
        return SimpleNamespace(
            adapter=adapter, config=config, type="calendar", id="x",
            created_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )

    assert ConnectionSummary.of(row("doctolib", {"device_trusted": False})).device_trusted is False
    assert ConnectionSummary.of(row("doctolib", {})).device_trusted is None
    assert ConnectionSummary.of(row("thevea", {"device_trusted": True})).device_trusted is None
