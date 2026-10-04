"""What the first real test call showed: a sandbox booking read as real, transcripts were stored
twice, the agent asked for a treatment it could not book, and phone numbers were unintelligible."""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession

_IDENTITY = dict(
    practice_name="Praxis Podo", street="Baumkirchner Straße 19", postcode="81673", city="München",
    phone="+498941115335", recipients=["team@example.org"], consent_policy_id="policy-v1",
)


# --- numbers are spoken so a caller can follow them -------------------------------------------


def _spoken(text: str) -> str:
    from app.workloads.conversational.surface import PhoneNumberSpeech

    return asyncio.run(PhoneNumberSpeech().filter(text))


def test_a_phone_number_is_spoken_in_short_groups_of_single_digits() -> None:
    spoken = _spoken("Ich habe Ihre Nummer als 015159830615 notiert.")

    assert "0 1 5, 1 5 9, 8 3 0, 6 1 5" in spoken
    assert not re.search(r"\d\d", spoken)


def test_an_international_number_with_spaces_is_grouped_the_same_way() -> None:
    spoken = _spoken("Ihre Nummer ist +49 151 59830615.")

    assert spoken.startswith("Ihre Nummer ist +")
    assert not re.search(r"\d\d", spoken)


@pytest.mark.parametrize(
    "text",
    ["am 06.10.2026 um 14:00 Uhr", "Baumkirchner Straße 19, 81673 München", "geboren 1991", "für 375 €"],
)
def test_dates_times_postcodes_years_and_prices_are_left_alone(text) -> None:
    assert _spoken(text) == text


# --- a sandbox booking is never presented as a real one ----------------------------------------


def _slot() -> str:
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return f"{day.isoformat()}T09:00"


def test_a_sandbox_test_booking_is_called_a_test(tmp_path, monkeypatch) -> None:
    """The first real test call said "gebucht" for a booking into memory."""
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("sandbox", test_mode="sandbox")
    session.set_details(
        first_name="Test", last_name="Patient", date_of_birth="1971-04-12",
        phone="0176 4289 9911", service_key="medizinische_fusspflege", preferred_time=_slot(),
    )
    session.find_patient()
    session.confirm("Test Patient.")

    result = session.book()

    assert result["status"] == "ok"
    assert "test calendar" in " ".join(result["agent_notes"])


# --- the transcript is stored once ------------------------------------------------------------


def test_one_spoken_sentence_is_stored_once_however_many_stages_it_passes() -> None:
    """Pipecat's observer sees a frame at every hop, so each sentence was stored twice,
    interleaved: "Gerne, ich schaue Gerne, nach ..."."""
    import uuid

    from pipecat.frames.frames import BotStoppedSpeakingFrame, TTSTextFrame
    from pipecat.observers.base_observer import FramePushed

    from app.workloads.conversational.recording import ConversationRecorder
    from app.workloads.conversational.surface import _TranscriptObserver

    class _Recorded(ConversationRecorder):
        def __init__(self) -> None:
            super().__init__(uuid.uuid4())
            self.events: list = []

        async def _append(self, kind, payload) -> None:
            self.events.append((kind, payload))

    recorder = _Recorded()
    observer = _TranscriptObserver(recorder, BookingSession("dedupe"))
    first, second = TTSTextFrame(text="Gerne,", aggregated_by="word"), TTSTextFrame(text="ich schaue.", aggregated_by="word")

    async def push(frame) -> None:
        await observer.on_push_frame(
            FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0)
        )

    async def main() -> None:
        for frame in (first, first, second, second):  # two hops each
            await push(frame)
        stopped = BotStoppedSpeakingFrame()
        await push(stopped)
        await push(stopped)

    asyncio.run(main())

    assert [payload["text"] for _, payload in recorder.events] == ["Gerne, ich schaue."]


# --- the agent speaks numbers well and never asks for a treatment it cannot book ---------------


def test_an_agent_with_one_plain_appointment_never_asks_which_treatment() -> None:
    from app.workloads.conversational.surface import _instructions

    prompt = _instructions("de", AgentConfig(**_IDENTITY))

    assert "never offer a list of treatments" in prompt
    assert "digit by digit" in prompt


def test_an_agent_with_a_treatment_list_still_asks_for_one() -> None:
    from app.workloads.conversational.surface import _instructions

    config = AgentConfig(
        **_IDENTITY, treatments=[{"key": "erstberatung", "name": "Erstberatung", "price_eur": 25}]
    )

    assert "never offer a list of treatments" not in _instructions("de", config)


# --- the phone number is read back as stored, not as the model remembers it ------------------


def test_the_phone_read_back_hands_the_agent_the_stored_digits() -> None:
    """Observed: 0151 5983 2613 stored correctly, read back as "null eins fünf eins neun…"."""
    session = BookingSession("phone")

    notes = " ".join(session.set_details(phone="0151 5983 2613")["agent_notes"])

    assert "015159832613" in notes and "never words" in notes


def test_the_handed_over_number_is_spoken_digit_by_digit() -> None:
    assert _spoken("Ich habe 015159832613 notiert.") == (
        "Ich habe 0 1 5, 1 5 9, 8 3 2, 6 1 3 notiert."
    )


def test_a_foreign_number_is_read_back_with_its_country_code() -> None:
    session = BookingSession("phone")

    notes = " ".join(session.set_details(phone="+1 650 362 8764")["agent_notes"])

    assert "+16503628764" in notes


def test_a_number_partly_in_words_is_refused_not_shortened() -> None:
    """Observed in a test: the words were dropped silently and +59832613 was stored."""
    from app.workloads.conversational.booking import normalize_phone

    assert normalize_phone("null eins fünf eins 5983 2613") is None
    session = BookingSession("phone")
    result = session.set_details(phone="null eins fünf eins 5983 2613")
    assert "phone" in result["rejected"] and "digits" in result["rejected"]["phone"]
    assert session.draft["phone"] == ""
    assert normalize_phone("+49 151 5983-2613") == "+4915159832613"
