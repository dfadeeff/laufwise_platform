"""Phone numbers and dates read as digits in a transcript; everything else is left as said."""

from __future__ import annotations

import pytest

from app.workloads.conversational.written_numbers import as_written, german_number


@pytest.mark.parametrize(
    ("said", "written"),
    [
        # Both from the test call of 4 October 2026.
        (
            "Das ist null eins fünf zwei fünf neun drei sechs zwei eins fünf sieben.",
            "Das ist 015259362157.",
        ),
        (
            "Ihre Telefonnummer ist null eins fünf zwei, fünf neun drei, sechs zwei eins, fünf sieben, richtig?",
            "Ihre Telefonnummer ist 015259362157, richtig?",
        ),
        ("Vierzehnter November?", "14.11.?"),
        (
            "Danke, den vierzehnten November neunzehnhundertzweiundneunzig habe ich notiert.",
            "Danke, den 14.11.1992 habe ich notiert.",
        ),
        ("Mein Geburtsdatum ist der dritte April einundsiebzig.", "Mein Geburtsdatum ist der 03.04.71."),
        ("Dreizehnter Juli zweiundneunzig.", "13.07.92."),
        ("am ersten Mai zweitausendeins", "am 01.05.2001"),
        ("It is oh one seven six four two eight nine.", "It is 01764289."),
    ],
)
def test_phone_numbers_and_dates_are_written_as_digits(said, written) -> None:
    assert as_written(said) == written


@pytest.mark.parametrize(
    "said",
    [
        "Ich möchte einen Termin nächste Woche vereinbaren.",
        "Machen wir halb elf.",
        "Montag um halb zehn, Montag um zehn Uhr oder Montag um halb elf.",
        "Wir bieten drei Behandlungen an.",
        "am nächsten Montag",
        "zwei oder drei",
    ],
)
def test_ordinary_speech_is_left_alone(said) -> None:
    assert as_written(said) == said


def test_a_word_after_the_month_that_is_not_a_year_is_kept() -> None:
    assert as_written("am vierzehnten November gerne") == "am 14.11. gerne"


@pytest.mark.parametrize(
    ("word", "number"),
    [("neunzehnhundertzweiundneunzig", 1992), ("zweitausendeins", 2001), ("einundsiebzig", 71),
     ("zwölf", 12), ("hundert", 100), ("Termin", None)],
)
def test_german_number_words(word, number) -> None:
    assert german_number(word) == number


def test_a_call_s_turns_are_stored_and_sent_to_the_test_page_written() -> None:
    import asyncio
    import uuid

    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.observers.base_observer import FramePushed

    from app.workloads.conversational.booking import BookingSession
    from app.workloads.conversational.recording import ConversationRecorder
    from app.workloads.conversational.surface import _TranscriptObserver

    class _Recorded(ConversationRecorder):
        def __init__(self) -> None:
            super().__init__(uuid.uuid4())
            self.events: list = []

        async def _append(self, kind, payload) -> None:
            self.events.append(payload)

    recorder, sent = _Recorded(), []
    observer = _TranscriptObserver(recorder, BookingSession("written"))

    async def send(data) -> None:
        sent.append(data)

    observer.send = send
    said = TranscriptionFrame(text="null eins fünf zwei fünf neun drei sechs", user_id="", timestamp="")

    asyncio.run(observer.on_push_frame(
        FramePushed(source=None, destination=None, frame=said, direction=None, timestamp=0)
    ))

    assert recorder.events[0]["text"] == "01525936"
    assert sent == [{"type": "transcript", "role": "caller", "text": "01525936"}]
