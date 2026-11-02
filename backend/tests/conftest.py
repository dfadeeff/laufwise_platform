"""The platform's own tests run against tests/fixtures/definitions, never the shipped templates
under backend/definitions (ADR-0039, success criterion 3): a demo module written to exercise
every platform guarantee, and the practice module, whose code still lives in the platform."""

from pathlib import Path

import pytest

from app.kernel import modules

FIXTURE_DEFINITIONS = Path(__file__).parent / "fixtures" / "definitions"


@pytest.fixture(autouse=True)
def _platform_definitions(monkeypatch):
    """The fixture definitions, published from their files. A test against the database replaces
    this with what is published there (`seeded`)."""
    monkeypatch.setattr(modules, "ROOT", FIXTURE_DEFINITIONS)
    monkeypatch.setattr(modules, "_published", {})
    modules.publish_from_files()
    yield
    modules._installed.cache_clear()
