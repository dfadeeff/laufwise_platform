"""M1 connector units (ADR-0004) — credential crypto, state routing, and the two calendar
connectors driven against httpx mock transports (no network, no real logins). The capture-
dependent GraphQL/REST operation strings are not exercised for content here."""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet

from laufwise.state.base import StateUnavailable, StateView

from app.config import settings
from app.connections import crypto
from app.connections.crypto import CredentialCryptoUnavailable
from app.connectors.base import Appointment
from app.providers.healthyfeet import HealthyfeetConnector, SourceError
from app.providers.routing import RoutingStateProvider
from app.providers.thevea import TheveaConnector, TheveaError


# --- credential crypto -------------------------------------------------------------------

def test_credential_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", Fernet.generate_key().decode())
    blob = "connector-credential-blob-v1"
    token = crypto.encrypt(blob)
    assert token != blob and crypto.decrypt(token) == blob


def test_crypto_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", None)
    with pytest.raises(CredentialCryptoUnavailable):
        crypto.encrypt("x")


# --- routing / anti-fabrication ----------------------------------------------------------

class _Stub:
    def __init__(self, value):
        self.value, self.calls = value, 0

    def query(self, name, params=None):
        self.calls += 1
        return StateView(value=self.value)


class _Raises:
    def query(self, name, params=None):
        raise StateUnavailable("down")


def test_routing_dispatches_and_never_fabricates():
    fixture = _Stub({"exists": True})  # a fabricated 'present' answer
    router = RoutingStateProvider(real={"source": _Raises()}, fallback=fixture)
    with pytest.raises(StateUnavailable):
        router.query("source_appt", {"provider": "source"})
    assert fixture.calls == 0  # real binding never falls back to fixture


# --- thevea destination connector (mock transport) ---------------------------------------

def _thevea(handler):
    return TheveaConnector("https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler))


def test_thevea_find_and_create():
    state = {"created": False}

    def handler(request):
        q = json.loads(request.content)["query"]
        if "Create" in q:
            state["created"] = True
            return httpx.Response(200, json={"data": {"create": {"ok": True}}})
        if "FindByRef" in q:
            nodes = [{"externalRef": "a1", "start": "2026-07-14"}] if state["created"] else []
            return httpx.Response(200, json={"data": {"appts": {"nodes": nodes}}})
        return httpx.Response(200, json={"data": {"login": {"ok": True}}})

    conn = _thevea(handler)
    assert conn.find_appointment("a1") is None          # not there yet
    conn.create_appointment(Appointment(ref="a1", start="2026-07-14"))
    assert conn.find_appointment("a1") is not None       # now findable


def test_thevea_graphql_error_raises():
    conn = _thevea(lambda r: httpx.Response(200, json={"errors": [{"message": "nope"}]}))
    with pytest.raises(TheveaError):
        conn.find_appointment("a1")


# --- healthyfeet source connector (mock transport) ---------------------------------------

def _healthyfeet(handler):
    return HealthyfeetConnector(
        "https://www.healthyfeet-podologie.de/api/admin",
        "u",
        "p",
        transport=httpx.MockTransport(handler),
    )


def test_healthyfeet_list_and_get():
    def handler(request):
        if request.url.path.endswith("/calendar"):
            return httpx.Response(200, json=[{"id": "a1", "start": "9:00"}, {"id": "a2", "start": "10:00"}])
        return httpx.Response(200, json={"id": "a1", "start": "9:00", "patient": "Müller"})

    conn = _healthyfeet(handler)
    appts = conn.list_appointments({"from": "2026-07-01", "to": "2026-07-31"})
    assert [a.ref for a in appts] == ["a1", "a2"]
    assert conn.get_appointment("a1").patient == "Müller"


def test_healthyfeet_transport_error_raises():
    conn = _healthyfeet(lambda r: httpx.Response(503))
    with pytest.raises(SourceError):
        conn.list_appointments({})