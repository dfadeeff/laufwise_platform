"""What to bring is said for the caller's payment category only (Mia spec v3 #11, #138).

A self-payer brings nothing; a statutory prescription means the original prescription, the
insurance card and an exemption certificate if there is one. Each category carries the practice's
own sentence (workspace data); it reaches the agent only in the result of a booking that went
through, and only the booked category's — so the agent has no other category's list to recite.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession

_KASSE_BRING = (
    "Bitte bringen Sie Ihre Heilmittelverordnung im Original und Ihre Versichertenkarte mit. "
    "Falls Sie eine Zuzahlungsbefreiung haben, bringen Sie bitte auch den entsprechenden Nachweis mit."
)
_CONFIG = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
    treatments=[{"key": "gross", "name": "Medizinische Fußpflege groß", "external_id": "1"}],
    payment_categories=[
        {"key": "selbstzahler", "label": "Selbstzahler"},
        {"key": "kasse", "label": "Heilmittelverordnung", "bring": _KASSE_BRING},
        {"key": "privat", "label": "Privatrezept", "bring": "Bitte bringen Sie Ihr Privatrezept mit."},
    ],
)


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _booked(category: str, **session) -> dict:
    practice = AgentConfig(**_CONFIG).to_practice()
    booking = BookingSession("bring", practice=practice, **session)
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    booking.set_details(
        first_name="Max", last_name="Buchmann", date_of_birth="1995-01-05",
        phone="015159730614", service_key="gross", payment_category=category,
        preferred_time=f"{day.isoformat()}T09:00",
    )
    booking.find_patient()
    label = practice.payment_category(category).label
    summary = (
        f"Max Buchmann, Medizinische Fußpflege groß, {label}, am Montag um neun Uhr in der "
        "Baumkirchner Straße 19. Ist das alles richtig?"
    )
    booking.hear("agent", summary)
    booking.hear("caller", "Ja, passt.")
    assert booking.confirm(summary)["confirmed"] is True
    result = booking.book()
    assert result["status"] == "ok"
    return result


def test_a_prescription_booking_is_told_to_bring_the_prescription_and_card() -> None:
    notes = " ".join(_booked("kasse")["agent_notes"])

    assert _KASSE_BRING in notes
    assert "Privatrezept" not in notes


def test_a_private_prescription_brings_only_that() -> None:
    notes = " ".join(_booked("privat")["agent_notes"])

    assert "Bitte bringen Sie Ihr Privatrezept mit." in notes
    assert "Versichertenkarte" not in notes


def test_a_self_payer_is_told_nothing_to_bring() -> None:
    notes = " ".join(_booked("selbstzahler")["agent_notes"])

    assert "Versichertenkarte" not in notes and "Privatrezept" not in notes
    assert "do not ask them to bring anything" in notes


def test_a_test_booking_in_the_sandbox_says_what_to_bring_too() -> None:
    notes = " ".join(_booked("kasse", test_mode="sandbox")["agent_notes"])

    assert "test calendar" in notes
    assert _KASSE_BRING in notes


def test_the_prompt_never_carries_a_categorys_list_to_recite() -> None:
    block = AgentConfig(**_CONFIG).to_practice().knowledge_block()

    assert "Versichertenkarte" not in block
    assert "what to bring" in block


def test_the_sentence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        AgentConfig(**{**_CONFIG, "payment_categories": [{"label": "Kasse", "bring": "x" * 301}]})
