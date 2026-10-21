"""The practice address is said exactly, its postcode digit by digit (Mia spec v3 #10, #137).

The live test said "81673" as "achtzehnhundertdreiundsiebzig": the model wrote the postcode in
words itself, as a different number. The prompt now carries the address as written and the
postcode as single digits in each language, and the voice filter reads the practice's postcode,
when the model writes it as digits, one digit at a time — so neither the model nor the
synthesiser makes it one number.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.surface import PhoneNumberSpeech, PostcodeSpeech

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)


def _spoken(text: str) -> str:
    async def both() -> str:
        return await PostcodeSpeech("81673").filter(await PhoneNumberSpeech().filter(text))

    return asyncio.run(both())


@pytest.mark.parametrize(
    "text,spoken",
    [
        ("Baumkirchner Straße 19, 81673 München.", "Baumkirchner Straße 19, 8 1 6 7 3 München."),
        ("Our address is Baumkirchner Straße 19, 81673 Munich.", "Our address is Baumkirchner Straße 19, 8 1 6 7 3 Munich."),
        ("Наш адрес: Baumkirchner Straße 19, 81673 Мюнхен.", "Наш адрес: Baumkirchner Straße 19, 8 1 6 7 3 Мюнхен."),
        ("Die Postleitzahl ist 81673.", "Die Postleitzahl ist 8 1 6 7 3."),
    ],
)
def test_a_postcode_is_spoken_digit_by_digit_in_every_language(text, spoken) -> None:
    assert _spoken(text) == spoken


@pytest.mark.parametrize(
    "text",
    ["am 06.10.2026 um 14:00 Uhr", "geboren 1991", "für 375 €", "Hausnummer 19", "12500 Euro", "Raum 8167"],
)
def test_dates_times_years_prices_and_house_numbers_are_left_alone(text) -> None:
    assert _spoken(text) == text


def test_phone_numbers_are_still_grouped() -> None:
    assert "0 1 5, 1 5 9, 8 3 0, 6 1 5" in _spoken("Ihre Nummer: 015159830615.")


def test_the_prompt_carries_the_exact_address_and_the_postcode_as_single_digits() -> None:
    block = AgentConfig(**_IDENTITY).to_practice().knowledge_block()

    assert "Address: Baumkirchner Straße 19, 81673 München" in block
    assert "acht eins sechs sieben drei" in block
    assert "eight one six seven three" in block
    assert "восемь один шесть семь три" in block
    assert "never as one number" in block


def test_a_postcode_that_is_not_digits_gets_no_digit_rule() -> None:
    block = AgentConfig(**{**_IDENTITY, "postcode": "SW1A 1AA"}).to_practice().knowledge_block()

    assert "Address: Baumkirchner Straße 19, SW1A 1AA München" in block
    assert "digit by digit" not in block
