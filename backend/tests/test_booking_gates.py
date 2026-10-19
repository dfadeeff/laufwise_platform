"""The booking contract holds the treatment itself, not only the prompt (Mia spec v3 #15, #142).

A treatment the caller chose is part of what they confirm: a read-back that does not name it is
refused, so a `service_key` the model recorded from an ambiguous phrase is caught by the caller
hearing it. And the treatment must still be one the agent may book at the moment of the write.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_TREATMENTS = [
    {"key": "klein", "name": "Podologische Komplexbehandlung Klein", "external_id": "12936",
     "aliases": {"de": ["Fußpflege"]}},
    {"key": "gross", "name": "Podologische Komplexbehandlung Groß", "external_id": "12937",
     "aliases": {"de": ["Fußpflege", "große Fußpflege"], "ru": ["полный педикюр"]}},
]


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _ready(treatments=_TREATMENTS, service_key="gross") -> BookingSession:
    session = BookingSession(
        "gates", practice=AgentConfig(**_IDENTITY, treatments=treatments).to_practice()
    )
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    session.set_details(
        first_name="Max", last_name="Buchmann", date_of_birth="1995-01-05",
        phone="015159730614", service_key=service_key, preferred_time=f"{day.isoformat()}T09:00",
    )
    session.find_patient()
    return session


def test_a_read_back_without_the_treatment_does_not_confirm() -> None:
    session = _ready()

    result = session.confirm("Max Buchmann, Montag neun Uhr, Baumkirchner Straße 19.")

    assert result["confirmed"] is False
    assert "Podologische Komplexbehandlung Groß" in result["agent_notes"][0]
    assert session.book()["status"] == "blocked"
    assert session.calendar.appointments == []


@pytest.mark.parametrize(
    "said", ["Podologische Komplexbehandlung Groß", "große Fußpflege", "полный педикюр"]
)
def test_the_practices_own_words_for_the_treatment_count(said) -> None:
    session = _ready()

    assert session.confirm(f"Max Buchmann, {said}, Montag neun Uhr.")["confirmed"] is True
    assert session.book()["status"] == "ok"


def test_a_phrase_that_fits_a_more_specific_treatment_does_not_confirm_the_other() -> None:
    """The model recorded the small one; the caller heard "große Fußpflege" and said yes."""
    session = _ready(service_key="klein")

    assert session.confirm("Max Buchmann, große Fußpflege, Montag neun Uhr.")["confirmed"] is False


def test_with_one_thing_to_book_the_caller_is_not_made_to_hear_it() -> None:
    session = _ready(treatments=[], service_key="")

    assert session.confirm("Max Buchmann, Montag neun Uhr.")["confirmed"] is True
    assert session.book()["status"] == "ok"


def test_a_treatment_that_is_no_longer_bookable_is_refused_at_the_write() -> None:
    """Recorded while it was bookable; the contract checks it again at the write, so a draft
    that reached it by any other route than the recording check cannot book either."""
    session = _ready()
    session.confirm("Max Buchmann, große Fußpflege, Montag neun Uhr.")
    practice = session._practice
    session._practice = replace(
        practice,
        services=tuple(replace(s, agent_bookable=s.key != "gross") for s in practice.services),
    )

    result = session.book()

    assert result["status"] == "blocked"
    assert "cannot be booked by phone" in result["reason"]
    assert session.calendar.appointments == []
