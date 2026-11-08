"""doctolib asks a device it trusts for the account's 4-digit PIN instead of an e-mailed code.

Once the server kept doctolib's browser state, the code stopped coming and a PIN screen came
instead: four one-digit password fields, `Zeichen 1/4` … `4/4`. The login took them for the
password field and stopped there. The practice enters the PIN once when connecting; it is stored
encrypted with the password, and every later login types it.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1 import connections as api
from app.connections import doctolib_login_jobs as jobs
from app.providers.doctolib import DoctolibError
from app.providers.doctolib_login import _pin_digits
from app.schemas.connection import DoctolibLoginStart


def test_the_pin_is_four_digits_or_nothing():
    assert DoctolibLoginStart(username="u", password="p", pin="1234").pin == "1234"
    assert DoctolibLoginStart(username="u", password="p").pin == ""
    for bad in ("123", "12345", "12a4"):
        with pytest.raises(ValidationError):
            DoctolibLoginStart(username="u", password="p", pin=bad)


def test_a_pin_screen_without_a_stored_pin_says_what_to_do():
    with pytest.raises(DoctolibError, match="PIN"):
        _pin_digits("")
    assert _pin_digits(" 12 34 ") == "1234"


def test_the_login_job_types_the_pin_it_was_given(monkeypatch):
    seen = {}
    done = threading.Event()

    def headless_login(username, password, get_code, *, pin_login="", browser_state="", pin=""):
        seen["pin"] = pin
        done.set()
        return {"_doctolib_session": "s"}

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    jobs.run_login(
        jobs.LoginJob(id="j", tenant_id="t", username="u", agenda_ids="", password="p", pin="4321"),
        lambda: None,
    )
    assert done.is_set()
    assert seen["pin"] == "4321"


def test_the_connection_stores_the_pin_with_the_password(monkeypatch):
    created = {}

    async def verify(adapter, config, creds):
        return None

    async def create_connection(session, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(id="c1")

    monkeypatch.setattr(api, "_verify_credentials", verify)
    monkeypatch.setattr(api.repo, "create_connection", create_connection)
    monkeypatch.setattr(api.crypto, "encrypt", lambda text: text)
    job = jobs.LoginJob(id="j", tenant_id="t", username="u", agenda_ids="", status="done", pin="4321")
    job.result = {"_doctolib_session": "s", "pin_login": "c", "browser_state": "state"}
    asyncio.run(api._finalize_doctolib_login(None, SimpleNamespace(id="t"), job))
    assert json.loads(created["tokens_enc"])["pin"] == "4321"


def test_a_reconnect_reuses_the_stored_pin(monkeypatch):
    async def list_connections(session, tenant_id):
        return [SimpleNamespace(adapter="doctolib", tokens_enc=json.dumps(
            {"username": "u", "pin_login": "c", "browser_state": "s", "pin": "4321"}))]

    monkeypatch.setattr(api.repo, "list_connections", list_connections)
    monkeypatch.setattr(api.crypto, "decrypt", lambda blob: blob)
    trust = asyncio.run(api._known_device_trust(None, SimpleNamespace(id="t"), "u"))
    assert trust["pin"] == "4321"


def test_an_import_relogin_types_the_stored_pin(monkeypatch):
    from app.connections import resolve

    seen = {}

    def headless_login(username, password, get_code, *, pin_login="", browser_state="", pin=""):
        seen["pin"] = pin
        return {"_doctolib_session": "fresh"}

    monkeypatch.setattr("app.providers.doctolib_login.headless_login", headless_login)
    creds = {"username": "u", "password": "p", "pin": "4321"}
    relogin = resolve._doctolib_relogin_opts(SimpleNamespace(id="conn-pin"), creds)["relogin"]
    assert relogin() == "fresh"
    assert seen["pin"] == "4321"
