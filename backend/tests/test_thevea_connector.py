"""M1 connector components (ADR-0003) — credential crypto, state routing, and the thevea client.

No DB, no network: the thevea client is driven against an httpx MockTransport that returns canned
GraphQL responses, so the session lifecycle and error->StateUnavailable mapping are tested without
the real API. (The three GraphQL operation strings are the only capture-dependent piece and are
not exercised for content here.)
"""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet

from laufwise.state.base import StateUnavailable, StateView

from app.config import settings
from app.connections import crypto
from app.connections.crypto import CredentialCryptoUnavailable
from app.providers.routing import RoutingStateProvider
from app.providers.thevea import TheveaClient, TheveaError, TheveaStateProvider


# --- credential crypto -------------------------------------------------------------------

def test_credential_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", Fernet.generate_key().decode())
    # crypto is credential-shape-agnostic; round-trip an opaque blob (no secret-shaped literal).
    plaintext = "thevea-credential-blob-v1"
    token = crypto.encrypt(plaintext)
    assert token != plaintext  # actually encrypted
    assert crypto.decrypt(token) == plaintext


def test_crypto_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", None)
    with pytest.raises(CredentialCryptoUnavailable):
        crypto.encrypt("x")


def test_crypto_wrong_key_fails(monkeypatch):
    monkeypatch.setattr(settings, "connection_enc_key", Fernet.generate_key().decode())
    token = crypto.encrypt("x")
    monkeypatch.setattr(settings, "connection_enc_key", Fernet.generate_key().decode())
    with pytest.raises(CredentialCryptoUnavailable):
        crypto.decrypt(token)


# --- routing / anti-fabrication ----------------------------------------------------------

class _StubProvider:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def query(self, name, params=None):
        self.calls += 1
        return StateView(value=self.value)


class _RaisingProvider:
    def query(self, name, params=None):
        raise StateUnavailable("source down")


def test_routing_dispatches_by_provider_name():
    real = _StubProvider({"has_free_slot": True})
    fixture = _StubProvider({"has_free_slot": False})
    router = RoutingStateProvider(real={"thevea": real}, fallback=fixture)

    view = router.query("calendar", {"provider": "thevea"})
    assert view.get_field("has_free_slot") is True
    assert real.calls == 1 and fixture.calls == 0


def test_routing_fixture_only_for_unregistered_provider():
    fixture = _StubProvider({"has_free_slot": False})
    router = RoutingStateProvider(real={"thevea": _StubProvider({})}, fallback=fixture)
    router.query("calendar", {"provider": "memory"})
    assert fixture.calls == 1  # memory binding -> fixture


def test_routing_real_provider_never_falls_back_to_fixture():
    """Anti-fabrication: a real-provider binding that fails raises — it must NOT serve fixture."""
    fixture = _StubProvider({"has_free_slot": True})  # a fabricated 'free' answer
    router = RoutingStateProvider(real={"thevea": _RaisingProvider()}, fallback=fixture)
    with pytest.raises(StateUnavailable):
        router.query("calendar", {"provider": "thevea"})
    assert fixture.calls == 0  # fixture never consulted for a real binding


# --- thevea client + provider (mock transport) -------------------------------------------

def _transport(handler):
    return httpx.MockTransport(handler)


def _ok(data):
    return httpx.Response(200, json={"data": data})


def test_client_login_then_availability_maps_state():
    seen = {"ops": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ops"] += 1
        body = json.loads(request.content)
        # login op returns nothing useful; availability returns a slots list
        if "Availability" in body["query"]:
            return _ok({"slots": [{"start": "2026-07-10T09:00", "free": True}]})
        return _ok({"login": {"ok": True}})

    client = TheveaClient("https://mein.thevea.de", "u", "p", transport=_transport(handler))
    provider = TheveaStateProvider(client)
    view = provider.query("calendar", {"vars": {"date": "2026-07-10", "start": "2026-07-10T09:00"}})
    assert view.get_field("has_free_slot") is True
    assert view.get_field("slot_booked") is False
    assert seen["ops"] == 2  # login + availability


def test_provider_maps_graphql_error_to_state_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "not authenticated"}]})

    client = TheveaClient("https://mein.thevea.de", "u", "p", transport=_transport(handler))
    with pytest.raises(StateUnavailable):
        TheveaStateProvider(client).query("calendar", {"vars": {"date": "2026-07-10"}})


def test_provider_maps_transport_error_to_state_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)  # thevea down

    client = TheveaClient("https://mein.thevea.de", "u", "p", transport=_transport(handler))
    with pytest.raises(StateUnavailable):
        TheveaStateProvider(client).query("calendar", {"vars": {"date": "2026-07-10"}})


def test_client_raises_theverror_on_bad_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = TheveaClient("https://mein.thevea.de", "u", "p", transport=_transport(handler))
    with pytest.raises(TheveaError):
        client.login()