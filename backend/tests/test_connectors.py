"""M1 connector units (ADR-0004) — credential crypto, state routing, and the two calendar
connectors driven against httpx mock transports (no network, no real logins). The capture-
dependent GraphQL/REST operation strings are not exercised for content here."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from laufwise.state.base import StateUnavailable, StateView

from app.config import settings
from app.connections import crypto
from app.connections.crypto import CredentialCryptoUnavailable
from app.connectors.base import Appointment
from app.providers.doctolib import DoctolibConnector, DoctolibError
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


def test_thevea_find_is_room_independent():
    """Idempotency searches ACROSS all configured rooms: an appointment written to one room is
    still found when this run's write-room is a different one (no cross-room duplicate)."""
    seen: dict = {}

    def handler(request):
        body = json.loads(request.content)
        if "getTermine" in body.get("query", ""):
            seen["personenIds"] = body["variables"]["personenIds"]
            # The appointment lives in room 208416, though this connector WRITES to 208413.
            return httpx.Response(200, json={"data": {"termine": [
                {"__typename": "SonstigerTermin", "id": 7, "from": "2026-07-14T09:00:00.000Z",
                 "bemerkung": "+49 · HF-a1", "mandantMitarbeiterId": 208416},
            ]}})
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    conn = TheveaConnector(
        "https://mein.thevea.de", "u", "p",
        room_id=208413, search_room_ids=[208413, 208416],
        transport=httpx.MockTransport(handler),
    )
    assert conn.find_appointment("HF-a1") is not None  # found though it's in the OTHER room
    assert seen["personenIds"] == [208413, 208416]  # one query covered both rooms


def test_thevea_create_payload_shape():
    """The write payload: title = 'name · procedure'; bemerkung = phone · dob · email · address,
    empties dropped, HF ref LAST (the idempotency key)."""
    captured: dict = {}

    def handler(request):
        body = json.loads(request.content)
        if body.get("operationName") == "addSonstigerTermin":
            captured.update(body["variables"]["input"]["terminInput"])
            return httpx.Response(
                200,
                json={"data": {"addSonstigerTermin": {"validationResult": {"type": "SUCCESS", "createdTermineIds": [1]}}}},
            )
        return httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}})

    _thevea(handler).create_appointment(
        Appointment(
            ref="HF-a1",
            start="2026-07-14 09:00:00+00",
            patient="Valentina Zeller-Klaus",
            raw={
                "service_label": "Eingewachsen",
                "phone": "+491622139879",
                "birth_date": "26-01-1988",
                "email": "v@example.com",
                "address": "",  # empty -> dropped, no blank gap
                "status": "confirmed",
            },
        )
    )
    assert captured["title"] == "Valentina Zeller-Klaus · Eingewachsen"
    # phone · dob · email, address dropped (empty), ref last
    assert captured["bemerkung"] == "+491622139879 · 26-01-1988 · v@example.com · HF-a1"


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


def test_thevea_verify_ok_then_rejects_bad_login():
    """Connect-time check: verify() authenticates; a login the system rejects raises TheveaError."""
    ok = _thevea(lambda r: httpx.Response(200, json={"data": {"benutzerLogin": {"benutzerkennung": "u"}}}))
    ok.verify()  # no raise
    # thevea returns a GraphQL error for a bad email (as the live INVALID_FORMAT_ERROR does).
    bad = _thevea(lambda r: httpx.Response(200, json={"errors": [{"message": "invalid email"}]}))
    with pytest.raises(TheveaError):
        bad.verify()


def test_healthyfeet_verify_rejects_bad_login():
    """verify() proves the admin credentials authenticate; a 401 surfaces as SourceError."""
    _healthyfeet(lambda r: httpx.Response(200, text="<html></html>")).verify()  # no raise
    with pytest.raises(SourceError):
        _healthyfeet(lambda r: httpx.Response(401)).verify()


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


# --- doctolib source connector (mock transport) ------------------------------------------

def _doctolib(handler, **kw):
    # session_cookie injected -> the connector treats itself as authed and never runs the
    # (capture-dependent) Keycloak login, so the read path is exercised in isolation.
    return DoctolibConnector(
        "https://pro.doctolib.de", "u", "p",
        agenda_ids="2570190,2557171", session_cookie="replayed",
        transport=httpx.MockTransport(handler), **kw,
    )


def _dl_appt(ref, start, first, last, status="confirmed", agenda=2570190):
    return {
        "id": ref, "agenda_id": agenda, "status": status, "visit_motive_id": 15882195,
        "start_date": start, "end_date": start, "patient": {
            "first_name": first, "last_name": last, "phone_number": "+4915100000000",
            "birthdate": "1990-01-01", "email": f"{first.lower()}@example.com",
            "address": "Teststr. 1", "zipcode": "80331", "city": "München",
        },
    }


def test_doctolib_lists_across_agendas_and_flattens_patient():
    """Reads once PER agenda_id, merges; flattens patient into the flat raw keys thevea reads;
    preserves status for the confirmed-only filter; maps the opaque id to ref."""
    seen_agendas = []

    def handler(request):
        agenda = request.url.params.get("agenda_ids")
        seen_agendas.append(agenda)
        # each agenda returns its own single appointment
        who = ("A1", "One") if agenda == "2570190" else ("B2", "Two")
        return httpx.Response(200, json={"data": [_dl_appt(f"ref-{agenda}", "2026-07-17T09:00:00.000+02:00", *who, agenda=int(agenda))], "meta": {}})

    conn = _doctolib(handler)
    appts = conn.list_appointments({"from": "2026-07-17", "to": "2026-07-17"})
    assert seen_agendas == ["2570190", "2557171"]  # one query per configured agenda
    assert {a.ref for a in appts} == {"ref-2570190", "ref-2557171"}
    a = appts[0]
    assert a.patient == "A1 One"
    assert a.raw["status"] == "confirmed"  # the orchestrator's safety filter reads this
    assert a.raw["phone"] == "+4915100000000" and a.raw["email"] == "a1@example.com"
    assert a.raw["address"] == "Teststr. 1, 80331 München"  # composed from split fields


def test_doctolib_get_appointment_finds_by_opaque_id():
    def handler(request):
        return httpx.Response(200, json={"data": [
            _dl_appt("tok-xyz", "2026-08-01T10:00:00.000+02:00", "Maik", "T"),
        ], "meta": {}})

    conn = _doctolib(handler)
    assert conn.get_appointment("tok-xyz").patient == "Maik T"
    assert conn.get_appointment("nope") is None


def test_doctolib_read_error_raises_doctolib_error():
    """A read failure must raise DoctolibError so SourceAppointmentProvider turns it into a BLOCK,
    never a false 'not present'."""
    conn = _doctolib(lambda r: httpx.Response(503))
    with pytest.raises(DoctolibError):
        conn.list_appointments({"from": "2026-07-17", "to": "2026-07-17"})


def test_doctolib_source_error_surfaces_as_state_unavailable():
    """The appointment.py coupling: a DoctolibError from get_appointment -> StateUnavailable."""
    from app.providers.appointment import SourceAppointmentProvider

    conn = _doctolib(lambda r: httpx.Response(500))
    with pytest.raises(StateUnavailable):
        SourceAppointmentProvider(conn, "tok-xyz").query("source_appt")


# --- connect-time credential validation (endpoint helper) --------------------------------

class _FakeConn:
    def __init__(self, on_verify):
        self._on_verify, self.closed = on_verify, False

    def verify(self):
        self._on_verify()

    def close(self):
        self.closed = True


def _run_verify(monkeypatch, on_verify, *, budget=8.0):
    """Drive the endpoint's async _verify_credentials with a faked connector."""
    from app.api.v1 import connections as conns

    fake = _FakeConn(on_verify)
    monkeypatch.setattr(conns.connectors_base, "build_connector", lambda *a, **k: fake)
    monkeypatch.setattr(conns, "_VERIFY_BUDGET_S", budget)
    asyncio.run(conns._verify_credentials("thevea", {}, {"username": "u", "password": "p"}))
    return fake


def test_connect_verify_passes_on_good_login(monkeypatch):
    fake = _run_verify(monkeypatch, lambda: None)
    assert fake.closed  # connector always cleaned up


def test_connect_verify_rejects_bad_login_fast(monkeypatch):
    """A system that AFFIRMATIVELY rejects the credentials -> 400 (the whole point of the check)."""
    with pytest.raises(HTTPException) as ei:
        _run_verify(monkeypatch, lambda: (_ for _ in ()).throw(RuntimeError("invalid email")))
    assert ei.value.status_code == 400 and "could not connect to thevea" in ei.value.detail


def test_connect_verify_allows_when_login_is_too_slow(monkeypatch):
    """A slow/unreachable system must NOT hang the connect form: exceeding the budget is allowed
    through (the import re-validates against real state). Regression guard for the 12s timeout."""
    _run_verify(monkeypatch, lambda: time.sleep(0.2), budget=0.03)  # no HTTPException raised