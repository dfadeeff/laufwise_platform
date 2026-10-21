"""Every booking has a confirmed payment category, compatible with its treatment (ADR-0027, #132).

The categories and which treatment each may be booked with are the practice's data. The platform
enforces them: the booking contract refuses without one, and an incompatible pair is never
recorded, whichever was said first.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents.config import AgentConfig
from app.workloads.conversational.booking import TOOLS, BookingSession
from app.workloads.conversational.surface import tool_properties

_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_CATEGORIES = [
    {
        "key": "muster13",
        "label": "Heilmittelverordnung (Muster 13)",
        "note": "Eine Verordnung bedeutet nicht, dass jede Leistung übernommen wird.",
    },
    {"key": "privatrezept", "label": "Privatrezept"},
    {"key": "selbstzahler", "label": "Selbstzahler"},
]
_TREATMENTS = [
    {
        "key": "komplex",
        "name": "Podologische Komplexbehandlung",
        "external_id": "12937",
        "payment_categories": ["muster13", "privatrezept"],
    },
    {"key": "hornhaut", "name": "Hornhaut entfernen", "external_id": "375959"},
]


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


def _config(**extra) -> AgentConfig:
    return AgentConfig(
        **_IDENTITY,
        treatments=_TREATMENTS,
        payment_question="Haben Sie eine ärztliche Heilmittelverordnung oder möchten Sie die "
        "Behandlung als Selbstzahler in Anspruch nehmen?",
        payment_categories=_CATEGORIES,
        **extra,
    )


def _session() -> BookingSession:
    return BookingSession("payment", practice=_config().to_practice())


def _weekday() -> date:
    day = date.today() + timedelta(days=14)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def _complete(session: BookingSession, **details) -> None:
    session.set_details(
        first_name="Max",
        last_name="Buchmann",
        date_of_birth="1995-01-05",
        phone="015159730614",
        preferred_time=f"{_weekday().isoformat()}T09:00",
        **details,
    )
    session.find_patient()
    name = next(t["name"] for t in _TREATMENTS if t["key"] == details["service_key"])
    paid = next((c["label"] for c in _CATEGORIES if c["key"] == details.get("payment_category")), "")
    session.confirm(f"Max Buchmann, {name}, {paid}, neun Uhr, Baumkirchner Straße 19.")


def test_payment_is_asked_first_with_the_practices_question() -> None:
    session = _session()

    result = session.set_details(preferred_time=f"{_weekday().isoformat()}T09:00")

    assert session.missing[0] == "payment_category"
    assert "Heilmittelverordnung oder möchten Sie" in result["agent_notes"][-1]
    assert "Never decide it for them" in result["agent_notes"][-1]


def test_the_practices_sentence_is_said_once_a_category_is_chosen() -> None:
    notes = _session().set_details(payment_category="muster13")["agent_notes"]

    assert any("nicht, dass jede Leistung übernommen wird" in n for n in notes)


def test_an_unknown_category_is_not_recorded() -> None:
    session = _session()

    reason = session.set_details(payment_category="kasse")["rejected"]["payment_category"]

    assert "muster13, privatrezept, selbstzahler" in reason
    assert session.draft["payment_category"] == ""


@pytest.mark.parametrize("order", ["category_first", "service_first"])
def test_an_incompatible_pair_is_refused_whichever_comes_first(order) -> None:
    session = _session()
    if order == "category_first":
        session.set_details(payment_category="selbstzahler")
        result = session.set_details(service_key="komplex")
        field = "service_key"
    else:
        session.set_details(service_key="komplex")
        result = session.set_details(payment_category="selbstzahler")
        field = "payment_category"

    reason = result["rejected"][field]
    assert "not booked as Selbstzahler" in reason
    assert "Heilmittelverordnung (Muster 13) or Privatrezept" in reason


def test_no_booking_without_a_payment_category() -> None:
    session = _session()
    _complete(session, service_key="hornhaut")

    result = session.book()

    assert result["status"] == "blocked"
    assert "paid for has not been settled" in result["reason"]
    assert session.calendar.appointments == []


def test_the_category_is_written_into_the_appointments_note() -> None:
    session = _session()
    _complete(session, service_key="komplex", payment_category="muster13")

    assert session.book()["status"] == "ok"
    (appointment,) = session.calendar.appointments
    assert appointment.raw["payment_category"] == "muster13"
    assert appointment.raw["details"].startswith("Heilmittelverordnung (Muster 13)")


def test_a_practice_that_does_not_ask_books_as_before() -> None:
    session = BookingSession(
        "payment", practice=AgentConfig(**_IDENTITY, treatments=_TREATMENTS).to_practice()
    )
    _complete(session, service_key="komplex")

    assert "payment_category" not in session.missing
    assert session.book()["status"] == "ok"


def test_the_model_is_offered_only_the_practices_categories() -> None:
    spec = next(s for s in TOOLS if s.name == "appointment_set_details")

    assert tool_properties(spec, _config())["payment_category"]["enum"] == [
        "muster13",
        "privatrezept",
        "selbstzahler",
    ]
    assert "payment_category" not in tool_properties(
        spec, AgentConfig(**_IDENTITY, treatments=_TREATMENTS)
    )


def test_the_agent_never_promises_cover() -> None:
    block = _config().to_practice().knowledge_block()

    assert "- privatrezept: Privatrezept" in block
    assert "never promise that a treatment is paid for or reimbursed" in block
    assert "Please ask the practice about insurance coverage" not in block


def test_category_keys_are_unique() -> None:
    with pytest.raises(ValueError, match="own name"):
        AgentConfig(**_IDENTITY, payment_categories=[_CATEGORIES[1], _CATEGORIES[1]])


def test_a_new_category_gets_a_key_from_its_name() -> None:
    config = AgentConfig(
        **_IDENTITY, payment_categories=[{"label": "Heilmittelverordnung (Muster 13)"}]
    )

    assert config.payment_categories[0].key == "heilmittelverordnung_muster_13"
