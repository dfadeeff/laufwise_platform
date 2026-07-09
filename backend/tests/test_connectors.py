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
    """login -> find (empty) -> create (addSonstigerTermin) -> find (matches on ref in bemerkung)."""
    state = {"created": False}

    def handler(request):
        body = json.loads(request.content)
        if body.get("operationName") == "addSonstigerTermin":  # persisted-query create
            state["created"] = True
            return httpx.Response(
                200,
                json={"data": {"addSonstigerTermin": {"validationResult": {"type": "SUCCESS", "createdTermineIds": [999]}}}},
            )
        q = body.get("query", "")
        if "getTermine" in q:
            termine = (
                [{"__typename": "SonstigerTermin", "id": 1, "from": "2026-07-14T09:00:00.000Z", "bemerkung": "HF-a1 · +49 · Nagel"}]
                if state["created"]
                else []
            )
            return httpx.Response(200, json={"data": {"termine": termine}})
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    conn = _thevea(handler)
    assert conn.find_appointment("HF-a1") is None  # not there yet
    conn.create_appointment(Appointment(ref="HF-a1", start="2026-07-14 09:00:00+00", patient="Müller"))
    assert conn.find_appointment("HF-a1") is not None  # now findable (ref matched in bemerkung)


def test_thevea_rejects_failed_create():
    def handler(request):
        if json.loads(request.content).get("operationName") == "addSonstigerTermin":
            return httpx.Response(200, json={"data": {"addSonstigerTermin": {"validationResult": {"type": "ERROR", "createdTermineIds": []}}}})
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    conn = _thevea(handler)
    with pytest.raises(TheveaError):
        conn.create_appointment(Appointment(ref="HF-a1", start="2026-07-14 09:00:00+00", patient="X"))


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
        # The calendar page embeds each booking as data-booking="{…HTML-escaped JSON…}".
        cards = "".join(
            '<button data-booking="{&quot;ref&quot;:&quot;%s&quot;,'
            '&quot;preferred_date&quot;:&quot;%s&quot;,&quot;name&quot;:&quot;%s&quot;}"></button>'
            % (ref, date, name)
            for ref, date, name in [
                ("a1", "2026-07-14 09:00", "Müller"),
                ("a2", "2026-07-15 10:00", "Schmidt"),
            ]
        )
        return httpx.Response(200, text=f"<html><body>{cards}</body></html>")

    conn = _healthyfeet(handler)
    appts = conn.list_appointments({"from": "2026-07-01", "to": "2026-07-31"})
    assert [a.ref for a in appts] == ["a1", "a2"]
    assert conn.get_appointment("a1").patient == "Müller"


def test_healthyfeet_transport_error_raises():
    conn = _healthyfeet(lambda r: httpx.Response(503))
    with pytest.raises(SourceError):
        conn.list_appointments({})