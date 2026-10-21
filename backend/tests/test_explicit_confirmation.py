"""Nothing is booked without the whole summary said and a clear yes heard (Mia spec v3 #9, #136).

The summary names the patient, the treatment, how it is paid for, the day, the time and the
place. The yes is checked by the platform against what the speech recognition heard after the
summary was spoken — the live test took "Yeah. Yeah. Passed." as consent.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import BookingSession
from app.workloads.conversational.consent import clear_yes

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_SUMMARY = (
    "Ich habe für Sie, Max Buchmann, eine medizinische Fußpflege groß am Montag um neun Uhr in "
    "unserer Praxis in der Baumkirchner Straße 19 vorgemerkt. Sie kommen als Selbstzahler. Ist das "
    "alles richtig und darf ich den Termin verbindlich buchen?"
)


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


@pytest.mark.parametrize(
    "said",
    ["Ja.", "Ja, genau, das passt so.", "Jawohl, bitte buchen.", "Yes, that's correct.",
     "Да, всё правильно.", "Okay, passt."],
)
def test_a_clear_yes(said) -> None:
    assert clear_yes(said)


@pytest.mark.parametrize(
    "said",
    ["Yeah. Yeah. Passed.", "Ja, aber am Dienstag.", "Ja?", "", "Moment mal.",
     "Nein, das stimmt nicht.", "Ja, und meine Frau auch.", "Нет.", "Ähm."],
)
def test_not_a_clear_yes(said) -> None:
    assert not clear_yes(said)


def _ready(**config) -> BookingSession:
    session = BookingSession(
        "confirm",
        practice=AgentConfig(
            **_IDENTITY,
            treatments=[
                {"key": "gross", "name": "Medizinische Fußpflege groß", "external_id": "1"},
                {"key": "klein", "name": "Medizinische Fußpflege klein", "external_id": "2"},
            ],
            payment_categories=[{"key": "selbstzahler", "label": "Selbstzahler"}],
            **config,
        ).to_practice(),
    )
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    session.set_details(
        first_name="Max", last_name="Buchmann", date_of_birth="1995-01-05",
        phone="015159730614", service_key="gross", payment_category="selbstzahler",
        preferred_time=f"{day.isoformat()}T09:00",
    )
    session.find_patient()
    return session


def _heard(session: BookingSession, *turns: tuple[str, str]) -> BookingSession:
    for role, said in turns:
        session.hear(role, said)
    return session


def test_the_summary_answered_with_a_clear_yes_confirms_and_books() -> None:
    session = _heard(_ready(), ("agent", _SUMMARY), ("caller", "Ja, passt."))

    assert session.confirm(_SUMMARY)["confirmed"] is True
    assert session.book()["status"] == "ok"


def test_the_live_tests_yeah_yeah_passed_is_not_consent() -> None:
    session = _heard(_ready(), ("agent", _SUMMARY), ("caller", "Yeah. Yeah. Passed."))

    result = session.confirm(_SUMMARY)

    assert result["confirmed"] is False
    assert "not a clear yes" in result["reason"]
    assert session.book()["status"] == "blocked"


def test_silence_after_the_summary_is_not_consent() -> None:
    session = _heard(_ready(), ("caller", "Ja, die Nummer stimmt."), ("agent", _SUMMARY))

    result = session.confirm(_SUMMARY)

    assert result["confirmed"] is False
    assert "not answered the summary" in result["reason"]


def test_a_summary_never_spoken_cannot_be_confirmed() -> None:
    session = _heard(_ready(), ("agent", "Einen Moment bitte."), ("caller", "Ja."))

    assert "has not been said" in session.confirm(_SUMMARY)["reason"]


def test_a_correction_is_not_a_yes() -> None:
    session = _heard(_ready(), ("agent", _SUMMARY), ("caller", "Ja, aber lieber am Dienstag."))

    assert session.confirm(_SUMMARY)["confirmed"] is False


@pytest.mark.parametrize(
    "drop,missing",
    [("Sie kommen als Selbstzahler. ", "how it is paid for"),
     (" in unserer Praxis in der Baumkirchner Straße 19", "where")],
)
def test_the_summary_must_say_how_it_is_paid_and_where(drop, missing) -> None:
    summary = _SUMMARY.replace(drop, "")
    session = _heard(_ready(), ("agent", summary), ("caller", "Ja."))

    result = session.confirm(summary)

    assert result["confirmed"] is False
    assert missing in result["reason"]
