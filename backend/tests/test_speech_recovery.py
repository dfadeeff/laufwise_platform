"""Unclear speech is recovered from, never acted on (Mia spec v3 #12, #139).

The live test: the echo of the greeting was transcribed as the caller, German was misheard as
English, and the same three times were read out three times. Each has a platform answer here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.tests.utils import run_test

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.echo import AgentSpeech, AgentSpeechTap, EchoGuard
from app.workloads.conversational.surface import MAX_KEYTERMS, recognition_terms

_GREETING = (
    "Guten Tag, hier ist Mia, die digitale Assistentin der Podologie Healthy Feet München. "
    "Wie kann ich Ihnen helfen?"
)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _speaking(text: str = _GREETING) -> tuple[AgentSpeech, _Clock]:
    clock = _Clock()
    speech = AgentSpeech(clock)
    speech.started()
    speech.said(text)
    return speech, clock


# --- the agent's own voice is not the caller ------------------------------------------------------


def test_the_greeting_heard_back_while_it_plays_is_an_echo() -> None:
    speech, _ = _speaking()

    assert speech.is_echo("die digitale Assistentin der Podologie Healthy Feet")


def test_an_echo_just_after_the_agent_stopped_is_still_an_echo() -> None:
    speech, clock = _speaking()
    speech.stopped()
    clock.now += 2.0

    assert speech.is_echo("Wie kann ich Ihnen helfen")


def test_the_same_words_said_later_are_the_caller() -> None:
    speech, clock = _speaking()
    speech.stopped()
    clock.now += 10.0

    assert not speech.is_echo("Wie kann ich Ihnen helfen")


def test_a_short_answer_over_the_agent_is_never_an_echo() -> None:
    speech, _ = _speaking("Passt Ihnen Montag um neun Uhr?")

    assert not speech.is_echo("Montag passt")
    assert not speech.is_echo("Ja")


def test_the_caller_talking_over_the_agent_is_heard() -> None:
    speech, _ = _speaking()

    assert not speech.is_echo("Ich brauche einen Termin für eine Fußpflege nächste Woche")


def test_the_guard_drops_the_echo_and_passes_the_caller() -> None:
    speech, _ = _speaking()
    echo = TranscriptionFrame("Wie kann ich Ihnen helfen", "u", "t")
    caller = TranscriptionFrame("Ich hätte gern einen Termin", "u", "t")

    down, _ = asyncio.run(run_test(EchoGuard(speech), frames_to_send=[echo, caller]))

    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["Ich hätte gern einen Termin"]


def test_the_tap_records_what_was_played_and_when() -> None:
    speech = AgentSpeech()

    asyncio.run(
        run_test(
            AgentSpeechTap(speech),
            frames_to_send=[
                BotStartedSpeakingFrame(),
                TTSTextFrame("Wie kann ich Ihnen helfen", aggregated_by="sentence"),
            ],
        )
    )

    assert speech.is_echo("wie kann ich ihnen helfen")
    speech.stopped()
    assert speech.is_echo("wie kann ich ihnen helfen")  # inside the tail


def test_one_frame_gets_one_answer() -> None:
    speech, clock = _speaking()
    speech.stopped()
    frame = TranscriptionFrame("Wie kann ich Ihnen helfen", "u", "t")

    assert speech.echo_frame(frame)
    clock.now += 60.0
    assert speech.echo_frame(frame)  # the observer asks later and gets the guard's answer


# --- recognition is weighted toward the practice's words ------------------------------------------


def test_the_practice_vocabulary_is_boosted_in_the_calls_language() -> None:
    config = AgentConfig(
        practice_name="Podologie Healthy Feet München",
        street="Baumkirchner Straße 19",
        city="München",
        treatments=[
            {
                "key": "gross",
                "name": "Medizinische Fußpflege groß",
                "aliases": {"de": ["Podologie", "Fußpflege"], "ru": ["подолог"]},
            }
        ],
        payment_categories=[{"key": "kasse", "label": "Heilmittelverordnung"}],
    )

    terms = recognition_terms(config, "de")

    assert terms[:3] == ["Podologie Healthy Feet München", "Baumkirchner Straße 19", "München"]
    assert {"Medizinische Fußpflege groß", "Podologie", "Fußpflege", "Heilmittelverordnung"} <= set(terms)
    assert "подолог" not in terms
    assert recognition_terms(None, "de") == []


def test_the_boost_list_is_bounded() -> None:
    config = AgentConfig(
        treatments=[
            {"key": f"t{i}", "name": f"Behandlung {i}", "aliases": {"de": [f"Name {i}"]}}
            for i in range(50)
        ]
    )

    assert len(recognition_terms(config, "de")) == MAX_KEYTERMS


# --- the same times are not read out again -------------------------------------------------------


def test_the_same_times_found_again_are_not_read_out_again(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("repeat")
    now = datetime(2026, 11, 2, 8, 0)

    first = session.search_availability(date_from="2026-11-03", now=now)
    again = session.search_availability(date_from="2026-11-03", now=now)

    assert first["slots"] and again["slots"] == first["slots"]
    assert "SAY these" in first["agent_notes"][0]
    assert "Do not read them out again" in again["agent_notes"][0]
