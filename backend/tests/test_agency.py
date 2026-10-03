"""An agency runs many practices from one login, and starts each from its practice type.

ADR-0016. A workspace is still a Clerk organization (ADR-0003 D2); an agency is a login that
belongs to several. Each workspace reports its own state through `/workspace/summary`, scoped by
the token like every other route, so the overview needs no new trust. Practice types are data: one
JSON file per type, applied when an agent is created.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.config import AgentConfig

# What only the practice can supply. Everything else a template must already make publishable.
_IDENTITY = dict(
    practice_name="Praxis Nord",
    street="Hauptstraße 1",
    postcode="80331",
    city="München",
    phone="+498912345678",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)


def test_every_template_is_a_publishable_agent_once_the_practice_adds_who_it_is() -> None:
    from app.agents.practice_types import load_practice_types

    types = load_practice_types()

    assert {"podiatry", "physiotherapy", "dental", "general_practice"} <= set(types)
    for key, template in types.items():
        assert template.label and template.description, key
        config = template.apply(name="Empfang", locale="de")
        assert AgentConfig.model_validate({**config.model_dump(), **_IDENTITY}).publish_issues() == [], key


def test_a_practice_type_carries_no_services_or_prices() -> None:
    """Services and prices are the practice's own, in its documents; a type cannot know them."""
    from app.agents.practice_types import load_practice_types

    for template in load_practice_types().values():
        assert template.apply(name="x", locale="de").treatments == []


def test_a_template_keeps_the_name_and_language_the_practice_chose() -> None:
    from app.agents.practice_types import load_practice_types

    config = load_practice_types()["dental"].apply(name="Lena", locale="en", practice_name="Zahnarzt Süd")

    assert (config.name, config.locale, config.practice_name) == ("Lena", "en", "Zahnarzt Süd")


def _app():
    from app.api.deps import current_tenant
    from app.api.v1 import agents

    app = FastAPI()
    app.include_router(agents.router, prefix="/agents")
    app.dependency_overrides[current_tenant] = lambda: SimpleNamespace(id="t")
    return app


def test_the_studio_is_offered_every_practice_type() -> None:
    offered = {t["key"]: t for t in TestClient(_app()).get("/agents/practice-types").json()}

    assert offered["physiotherapy"]["label"] and offered["physiotherapy"]["description"]


def test_an_agent_cannot_be_created_from_a_practice_type_that_does_not_exist() -> None:
    from app.api.v1 import agents

    app = _app()
    app.dependency_overrides[agents.get_session] = lambda: None

    response = TestClient(app).post("/agents", json={"name": "x", "practice_type": "astrology"})

    assert response.status_code == 422
