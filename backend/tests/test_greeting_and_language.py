"""Mia greets once, as an AI assistant of the practice by its real name, and follows the caller's
language without restarting (Mia spec v3 #1–#2, #128 and #129).

The live test: she introduced herself again after "Алло", refused to continue in Russian, and said
"One moment, please" inside a German call. The Studio prompt (`studio.md`) — the one every
published agent runs — had no language rules at all.
"""

from __future__ import annotations

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.surface import CallLanguage, _instructions


# --- the words the platform says itself follow the caller, not one misheard turn ---------------


def test_one_turn_misheard_as_english_does_not_switch_a_german_call() -> None:
    call = CallLanguage("de")

    call.heard("en", "Last time determine.")

    assert call.now == "de"


def test_two_english_turns_in_a_row_do_switch() -> None:
    call = CallLanguage("de")

    call.heard("en", "Sorry, could we do this in English?")
    call.heard("en", "I need an appointment on Monday.")

    assert call.now == "en"


def test_a_german_turn_in_between_resets_the_doubt() -> None:
    call = CallLanguage("de")

    call.heard("en", "Last time determine.")
    call.heard("de", "Ich brauche einen Termin.")
    call.heard("en", "Pascale School.")

    assert call.now == "de"


def test_russian_in_cyrillic_switches_at_once() -> None:
    call = CallLanguage("de")

    call.heard("ru", "Извините, можно по-русски?")

    assert call.now == "ru"


def test_russian_tagged_on_latin_text_needs_a_second_turn() -> None:
    call = CallLanguage("de")

    call.heard("ru", "Ja genau")

    assert call.now == "de"


@pytest.mark.parametrize("tag", ["", "fr", "xx"])
def test_an_unknown_or_missing_language_changes_nothing(tag) -> None:
    call = CallLanguage("ru")

    call.heard(tag, "bonjour")
    call.heard(tag, "bonjour")

    assert call.now == "ru"


# --- what the Studio prompt tells every published agent -------------------------------------------


def _prompt(language: str = "de") -> str:
    config = AgentConfig(
        name="Mia",
        practice_name="Podologie Healthy Feet München",
        street="Baumkirchner Straße 19",
        postcode="81673",
        city="München",
        recipients=["team@example.org"],
        consent_policy_id="policy-v1",
    )
    return _instructions(language, config)


def test_she_is_an_ai_assistant_of_the_practice_by_its_exact_name() -> None:
    prompt = _prompt()

    assert "You are Mia, the AI assistant for Podologie Healthy Feet München." in prompt
    assert "KI-Assistentin" in prompt and "ИИ-помощник" in prompt
    assert "never shorten it" in prompt


def test_she_greets_once_and_a_line_check_is_not_a_new_call() -> None:
    prompt = _prompt()

    assert "Greet once" in prompt
    assert "Алло?" in prompt and "can hear them" in prompt


def test_she_switches_between_the_three_and_never_refuses_one() -> None:
    prompt = _prompt("ru")

    assert "Start in Russian" in prompt
    assert "never refuse one of them" in prompt
    assert "keep every detail already" in prompt
    assert "was misheard, not a switch" in prompt
