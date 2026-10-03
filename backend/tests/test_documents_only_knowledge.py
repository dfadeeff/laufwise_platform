"""Services and prices live in the practice's documents; the agent books a plain appointment.

The Studio no longer has a treatment list. An agent without one books a generic appointment of the
configured length, never asks which treatment, and quotes services and prices only from documents.
Agents that still carry a treatment list (created before this change) keep it.
"""

from __future__ import annotations


from app.agents.config import AgentConfig
from app.workloads.conversational.booking import TOOLS, BookingSession
from app.workloads.conversational.surface import tool_properties

_IDENTITY = dict(
    practice_name="Praxis Nord", street="Hauptstraße 1", postcode="80331", city="München",
    phone="+498912345678", recipients=["team@example.org"], consent_policy_id="policy-v1",
)


def test_an_agent_without_a_treatment_list_can_be_published() -> None:
    assert AgentConfig(**_IDENTITY).publish_issues() == []


def test_without_treatments_the_agent_books_one_plain_appointment() -> None:
    practice = AgentConfig(**_IDENTITY, locale="de").to_practice()

    (service,) = practice.bookable_services
    assert (service.key, service.name, service.default) == ("appointment", "Termin", True)


def test_the_plain_appointment_is_chosen_without_asking_which_treatment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))
    session = BookingSession("plain", practice=AgentConfig(**_IDENTITY).to_practice())

    assert "service_key" not in session.missing


def test_the_model_is_offered_exactly_the_appointment_it_can_book() -> None:
    spec = next(s for s in TOOLS if "service_key" in s.properties)

    assert tool_properties(spec, AgentConfig(**_IDENTITY))["service_key"]["enum"] == ["appointment"]


def test_prices_are_answered_from_the_documents_not_from_an_empty_list() -> None:
    """With no list, "price on request" for a single appointment would contradict the documents."""
    block = AgentConfig(**_IDENTITY).to_practice().knowledge_block()

    assert "price on request" not in block
    assert "documents" in block


def test_an_agent_that_kept_its_treatment_list_still_books_from_it() -> None:
    practice = AgentConfig(
        **_IDENTITY, treatments=[{"key": "erstberatung", "name": "Erstberatung", "price_eur": 25}]
    ).to_practice()

    assert [s.key for s in practice.bookable_services] == ["erstberatung"]
    assert "Erstberatung" in practice.knowledge_block()
