"""The Betriebskosten forms as the platform reads them (forms/*.json): the input is the
definition's own case, money never passes through a float, the result leaves out what the
platform shows itself."""

from __future__ import annotations

import pytest

from app.kernel.modules import workflow
from app.extraction.strict import strict
from app.kernel.schema import empty


@pytest.fixture
def betriebskosten():
    return workflow("betriebskosten")[1]


def test_the_input_form_is_the_definitions_own_case(betriebskosten) -> None:
    schema = betriebskosten.schemas()["input"]["properties"]

    assert list(schema) == [
        "unit", "period_start", "period_end", "positions", "tenancies", "lease", "heating",
        "today", "rent_increase",  # the working day, and a § 558 request beside the settlement
    ]
    assert schema["rent_increase"]["nullable"] is True
    assert "cap_percent" not in schema["rent_increase"]["properties"]  # the assessment's, not a person's
    assert schema["period_start"] == {"type": "string", "format": "date",
                                      "title": "Abrechnungszeitraum von"}
    position = schema["positions"]["items"]["properties"]
    assert position["amount"]["format"] == "decimal"  # money never passes through a float
    assert {"value": "kabel_sammelvertrag", "label": "Kabelanschluss (Sammelvertrag)"} in (
        position["category"]["enum"]
    )
    assert position["category"]["nullable"] is True
    assert schema["heating"]["nullable"] is True
    assert schema["heating"]["properties"]["readings"]["type"] == "array"


def test_the_result_form_leaves_out_what_the_platform_shows_itself(betriebskosten) -> None:
    result = betriebskosten.schemas()["result"]["properties"]
    assert "questions" not in result and "assumptions" not in result
    assert result["statements"]["items"]["properties"]["lines"]["type"] == "array"


def test_a_new_form_starts_blank(betriebskosten) -> None:
    blank = empty(betriebskosten.schemas()["input"])
    assert blank["positions"] == [] and blank["heating"] is None and blank["unit"] == ""


def test_the_model_must_return_every_field_and_nothing_else(betriebskosten) -> None:
    FORM = betriebskosten.schemas()["input"]
    schema = strict(FORM)

    assert schema["additionalProperties"] is False
    assert schema["required"] == list(FORM["properties"])
    position = schema["properties"]["positions"]["items"]
    assert position["required"] == list(FORM["properties"]["positions"]["items"]["properties"])
    assert position["properties"]["amount"]["pattern"]  # decimals stay strings
    assert None in position["properties"]["category"]["enum"]  # "unsure" is allowed
    heating = schema["properties"]["heating"]
    assert {"type": "null"} in heating["anyOf"]
