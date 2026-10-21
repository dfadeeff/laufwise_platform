"""A workspace's service catalogue: every booking names a specific, linked service (ADR-0025).

Mia specification v3, #130: identify the treatment before booking, never fall back to a generic
one, and book only services linked to the calendar system's own id. An unlinked service is offered
as a callback; the booking tool does not even accept it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.config import AgentConfig
from app.api.v1.services import _key
from app.workloads.conversational.booking import BookingSession
from tests.test_agent_workspace_db import URL, _other_tenant, complete_config, workspace  # noqa: F401 — fixture

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München", street="Baumkirchner Straße 19",
    postcode="81673", city="München", phone="+498941115335", recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_CATALOGUE = [
    {"key": "komplex_gross", "name": "Podologische Komplexbehandlung Groß", "external_id": "12937",
     "duration_minutes": 45, "aliases": {"de": ["große Fußpflege"], "ru": ["полный педикюр"]}},
    {"key": "beratung", "name": "Beratung", "external_id": "380625", "consultation": True},
    {"key": "warze", "name": "Warze abtragen"},  # not linked to Thevea yet
    {"key": "kaltplasma", "name": "Kaltplasma", "external_id": "1", "bookable": False},  # switched off
]


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _config(**extra) -> AgentConfig:
    return AgentConfig(**_IDENTITY, treatments=_CATALOGUE, **extra)


def test_only_switched_on_and_linked_services_are_bookable_by_phone() -> None:
    practice = _config().to_practice()

    assert [s.key for s in practice.bookable_services] == ["komplex_gross", "beratung"]
    assert practice.service("komplex_gross").service_id == "12937"
    assert practice.service("komplex_gross").duration_minutes == 45
    assert practice.service("warze").handoff_reason == "it is not set up for booking by phone yet"


def test_nothing_is_picked_for_the_caller() -> None:
    """With several services the treatment stays open until the caller names one."""
    session = BookingSession("catalogue", practice=_config().to_practice())

    assert session.draft["service_key"] == ""
    assert "service_key" in session.missing


def test_an_unlinked_service_is_refused_with_a_callback_not_booked() -> None:
    session = BookingSession("catalogue", practice=_config().to_practice())

    result = session.set_details(service_key="warze")

    assert "not set up for booking by phone" in result["rejected"]["service_key"]
    assert "callback" in result["rejected"]["service_key"]


def test_an_unknown_treatment_never_suggests_a_default() -> None:
    session = BookingSession("catalogue", practice=_config().to_practice())

    reason = session.set_details(service_key="fusspflege")["rejected"]["service_key"]

    assert "ask which of its treatments" in reason and "usual" not in reason


def test_the_booking_names_the_service_and_its_thevea_id() -> None:
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    session = BookingSession("catalogue", practice=_config().to_practice())
    session.set_details(
        first_name="Max", last_name="Buchmann", date_of_birth="1995-01-05", phone="015159730614",
        service_key="komplex_gross", preferred_time=f"{day.isoformat()}T09:00",
    )
    session.find_patient()
    session.confirm("Max Buchmann, große Fußpflege, neun Uhr, Baumkirchner Straße 19.")

    assert session.book()["status"] == "ok"
    (appointment,) = session.calendar.appointments
    assert appointment.raw["service_label"] == "Podologische Komplexbehandlung Groß (Leistung 12937)"
    assert appointment.raw["service_id"] == "12937"


def test_the_agent_is_told_the_services_and_never_to_pick_one() -> None:
    from app.workloads.conversational.surface import _instructions

    prompt = _instructions("de", _config())

    assert "Never pick one for them" in prompt
    assert "- beratung: Beratung — consultation" in prompt
    assert "- warze: Warze abtragen — NOT bookable by phone" in prompt
    assert "callers say: de: große Fußpflege; ru: полный педикюр" in prompt
    assert "This practice books one kind of appointment" not in prompt


def test_a_workspace_without_a_catalogue_keeps_its_plain_appointment() -> None:
    practice = AgentConfig(**_IDENTITY).to_practice()

    assert practice.has_no_price_list and len(practice.bookable_services) == 1


@pytest.mark.parametrize(
    ("name", "taken", "key"),
    [
        ("Hornhaut entfernen", set(), "hornhaut_entfernen"),
        ("Fußpflege – groß", set(), "fusspflege_gross"),
        ("Hühnerauge", set(), "huhnerauge"),
        ("Beratung", {"beratung"}, "beratung_2"),
    ],
)
def test_a_service_gets_a_stable_readable_key(name, taken, key) -> None:
    assert _key(name, taken) == key


@pytest.mark.skipif(not URL, reason="requires STUDIO_TEST_DATABASE_URL")
def test_the_catalogue_is_workspace_data_pinned_at_publish(workspace) -> None:  # noqa: F811
    client, owner, _, _, maker = workspace
    created = client.post(
        "/api/v1/services",
        json={"name": "Podologische Komplexbehandlung Groß", "external_id": "12937",
              "duration_minutes": 45, "aliases": {"de": ["große Fußpflege"]},
              "payment_categories": ["muster13", "privatrezept"]},
    )
    assert created.status_code == 200, created.text
    assert created.json()["phone_bookable"] is True
    assert created.json()["payment_categories"] == ["muster13", "privatrezept"]
    unlinked = client.post("/api/v1/services", json={"name": "Warze abtragen"}).json()
    assert unlinked["phone_bookable"] is False

    agent = client.post("/api/v1/agents").json()
    path = "/api/v1/agents/" + agent["id"]
    client.post(path + "/draft", json={"generation": 1, "config": complete_config()}).raise_for_status()
    client.post(path + "/publish", json={"generation": 2}).raise_for_status()
    pinned = client.get(path).json()["history"][0]["config"]["treatments"]
    assert [(t["key"], t["external_id"]) for t in pinned] == [
        ("podologische_komplexbehandlung_gross", "12937"),
        ("warze_abtragen", ""),
    ]
    assert [t["payment_categories"] for t in pinned] == [["muster13", "privatrezept"], []]

    # The Studio's "try a phrase" uses the same matching a call does.
    tried = client.post("/api/v1/services/recognise", json={"said": "eine große Fußpflege"}).json()
    assert [m["name"] for m in tried["matches"]] == ["Podologische Komplexbehandlung Groß"]

    # Editing the catalogue changes no published agent until it is published again.
    client.put(f"/api/v1/services/{created.json()['id']}", json={"name": "Groß", "external_id": "999"})
    assert client.get(path).json()["history"][0]["config"]["treatments"][0]["external_id"] == "12937"

    # Another workspace sees none of it.
    owner.id = _other_tenant(maker)
    assert client.get("/api/v1/services").json() == []
    assert client.delete(f"/api/v1/services/{created.json()['id']}").status_code == 404
