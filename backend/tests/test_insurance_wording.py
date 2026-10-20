"""Treatment on a statutory prescription is never promised as free (Mia spec v3 #7, #134).

A live call said it was: a document copied from the website read "dann zahlen Sie nichts". The
co-payment wording is now always in front of the agent, editable but never absent, and a 0 € price
from the website is said as that wording, never as a figure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents.config import DEFAULT_INSURANCE_WORDING, AgentConfig
from app.prices.page import parse_prices
from app.prices.sync import merged, with_prices
from app.workloads.conversational.booking import BookingSession
from tests.test_website_prices import PAGE

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_STATUTORY = "Podologische Behandlung mit Heilmittelverordnung (Rezept)"


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


@pytest.mark.parametrize(
    "categories",
    [[], [{"key": "muster13", "label": "Heilmittelverordnung (Muster 13)"}]],
    ids=["no-categories", "with-categories"],
)
def test_the_co_payment_wording_and_the_never_free_rule_are_always_there(categories) -> None:
    block = AgentConfig(**_IDENTITY, payment_categories=categories).to_practice().knowledge_block()

    assert DEFAULT_INSURANCE_WORDING in block
    assert "zehn Euro pro Rezept plus zehn Prozent" in block
    assert "is NEVER free for the patient" in block
    assert "Please ask the practice about insurance coverage" not in block


def test_a_practice_words_it_its_own_way() -> None:
    own = "Mit Rezept zahlen Erwachsene die gesetzliche Zuzahlung: 10 € je Verordnung und 10 %."
    block = AgentConfig(**_IDENTITY, insurance_wording=own).to_practice().knowledge_block()

    assert own in block and DEFAULT_INSURANCE_WORDING not in block


def test_a_zero_euro_price_on_the_website_is_said_as_the_co_payment_never_as_free() -> None:
    price_list = type("L", (), {})()
    now = datetime(2026, 10, 20, tzinfo=UTC)
    price_list.rows, price_list.checked_at, price_list.error = merged([], parse_prices(PAGE), now), now, ""
    config = AgentConfig(
        **_IDENTITY,
        treatments=[{"key": "kasse", "name": "Podologie mit Rezept", "price_label": _STATUTORY}],
    )

    result = BookingSession("free", practice=with_prices(config, price_list).to_practice()).get_service_price(
        "kasse"
    )

    assert result["price"] is None and result["statutory"] is True
    assert "NOT free" in result["agent_notes"][0]
    assert DEFAULT_INSURANCE_WORDING in result["agent_notes"][0]
