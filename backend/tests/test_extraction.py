"""Reading documents into a case's input (ADR-0033).

The model fills the form and nothing else; every amount, share and date it read is looked up in
the documents' text, and what is not there is listed for checking; a model's reading is never
computed before a person confirms it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_tenant
from app.api.v1 import workflow_agents as routes
from app.extraction.provenance import found_in, unconfirmed
from app.extraction.reader import Document, OpenAIReader, ReaderUnavailable
from app.extraction.strict import strict
from app.files import store as store_module
from app.files.store import LocalBlobStore
from app.kernel.modules import workflow
from app.main import app
from tests.test_workflow_agents import SETTINGS, _as, _cleanup, _fixture, _PROPERTY, db


# --- the schema the model must fill -------------------------------------------------------


def test_the_model_must_return_every_field_and_nothing_else() -> None:
    form = workflow("umlage")[1].schemas()["input"]
    schema = strict(form)

    assert schema["additionalProperties"] is False
    assert schema["required"] == list(form["properties"])
    item = schema["properties"]["items"]["items"]
    assert item["required"] == list(form["properties"]["items"]["items"]["properties"])
    assert item["properties"]["amount"]["pattern"]  # decimals stay strings
    assert None in item["properties"]["category"]["enum"]  # "unsure" is allowed


# --- what the documents say ---------------------------------------------------------------


TEXT = """Hausgeld gesamt 3.603,00 €  Müllabfuhr 198,00 €  Hauswart (darin enthalten 78,00 €)
40 % Grundkosten, 60 % Verbrauchskosten  Verbrauchsanteil 68 %  50/50 Verbrauch/Fläche
Mietverhältnis bis einschließlich 30.06.2025, Mietbeginn 01.03.2021"""


@pytest.mark.parametrize(
    ("value", "fmt", "found"),
    [
        ("3603.00", "decimal", True),  # 3.603,00
        ("198.00", "decimal", True),
        ("78", "decimal", True),
        ("0.40", "decimal", True),  # 40 %
        ("0.68", "decimal", True),  # 68 %
        ("0.50", "decimal", True),  # 50/50
        ("2025-06-30", "date", True),
        ("2021-03-01", "date", True),
        ("199.00", "decimal", False),
        ("0.32", "decimal", False),
        ("2025-07-31", "date", False),
    ],
)
def test_a_value_is_confirmed_only_when_the_documents_print_it(value, fmt, found) -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string", "format": fmt, "title": "X"}}}
    misses = unconfirmed({"x": value}, schema, found_in([TEXT]))
    assert (misses == []) is found


def test_misses_name_where_they_are() -> None:
    data = {"positions": [{"amount": "198.00"}, {"amount": "199.99"}]}
    schema = {
        "type": "object",
        "properties": {
            "positions": {
                "type": "array",
                "title": "Positionen",
                "items": {
                    "type": "object",
                    "properties": {"amount": {"type": "string", "format": "decimal", "title": "Betrag"}},
                },
            }
        },
    }
    assert unconfirmed(data, schema, found_in([TEXT])) == ["Positionen #2 · Betrag: 199.99"]


# --- the reader ---------------------------------------------------------------------------


def _reader(handler) -> tuple[OpenAIReader, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return OpenAIReader("key", "test-model", transport=httpx.MockTransport(record)), seen


def test_the_reader_sends_the_pdf_itself_and_a_strict_schema() -> None:
    answer = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"unit": "W3"}'}]}]}
    reader, seen = _reader(lambda r: httpx.Response(200, json=answer))

    got = asyncio.run(
        reader.read({"type": "object"}, "GUIDE", [Document("a.pdf", "application/pdf", b"%PDF-1.7")])
    )

    assert got == {"unit": "W3"}
    body = json.loads(seen[0].content)
    assert body["model"] == "test-model"
    assert body["text"]["format"]["strict"] is True
    assert "GUIDE" in body["input"][0]["content"][0]["text"]
    file_part = body["input"][1]["content"][1]
    assert file_part["type"] == "input_file"
    assert file_part["file_data"].startswith("data:application/pdf;base64,")


def test_the_reader_is_told_which_flat_of_several_the_case_is_about() -> None:
    # NK-04: one landlord's documents for two flats; each flat is a case of its own.
    answer = {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"unit": "W2"}'}]}]}
    reader, seen = _reader(lambda r: httpx.Response(200, json=answer))
    asyncio.run(reader.read({"type": "object"}, "G", [Document("a.pdf", "application/pdf", b"%PDF")], about="Wohnung Nr. 2 · 2024"))
    asked = json.loads(seen[0].content)["input"][1]["content"][-1]["text"]
    assert "The case is: Wohnung Nr. 2 · 2024" in asked and "read only what belongs to it" in asked


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(500, text="boom"), "refused"),
        (httpx.Response(200, json={"output": []}), "no form"),
        (
            httpx.Response(200, json={"output": [{"content": [{"type": "refusal", "refusal": "no"}]}]}),
            "declined",
        ),
        (
            httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": "{"}]}]}),
            "valid JSON",
        ),
    ],
)
def test_a_reader_that_does_not_answer_usefully_changes_nothing(response, match) -> None:
    reader, _ = _reader(lambda r: response)
    with pytest.raises(ReaderUnavailable, match=match):
        asyncio.run(reader.read({}, "", [Document("a.txt", "text/plain", b"x")]))


# --- the API, against the database ---------------------------------------------------------

client = TestClient(app)


class _FakeReader:
    """Reads the demo case u1 as the fixture says, plus one amount the documents do not contain."""

    model = "fake-reader"

    async def read(self, schema, guide, documents, about=""):
        self.about = about  # the case's title, so the reader knows which flat or matter is meant
        data = _fixture("u1")
        data["items"][0]["amount"] = "171.46"  # misread: the document says 171,40
        return data


@pytest.fixture
def property_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_store", LocalBlobStore(tmp_path))
    monkeypatch.setattr(routes, "document_reader", lambda: _FakeReader())
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "demo")
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


@db
def test_a_reading_is_checked_against_the_documents_and_confirmed_before_it_is_computed(
    property_workspace,
) -> None:
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "umlage", "name": "A"}
    ).json()
    client.put(
        f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS}
    )
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W3"}).json()
    assert client.post(f"/api/v1/cases/{case['id']}/extract").status_code == 422  # no documents

    document = "Reinigung 171,40 €\nHausmeisterbedarf 40,00 €\n".encode()
    file_id = client.post("/api/v1/files", params={"name": "rechnung.txt"}, content=document).json()["id"]
    client.post(f"/api/v1/cases/{case['id']}/files", json={"file_id": file_id})

    read = client.post(f"/api/v1/cases/{case['id']}/extract").json()
    assert read["input_source"] == "extracted"
    assert any(item.endswith(": 171.46") for item in read["to_check"])  # the misread is listed
    assert not any(item.endswith(": 40.00") for item in read["to_check"])  # found in the text
    assert read["events"][-1]["kind"] == "extracted"
    assert read["events"][-1]["model"] == "fake-reader"

    refused = client.post(f"/api/v1/cases/{case['id']}/compute")
    assert refused.status_code == 409

    corrected = {**read["input"]}
    corrected["items"][0]["amount"] = "171.40"
    saved = client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": read["input_revision"], "input": corrected},
    ).json()
    assert saved["input_source"] == "person" and saved["to_check"] == []

    computed = client.post(f"/api/v1/cases/{case['id']}/compute").json()
    assert computed["status"] == "ready"
    assert computed["result"]["statements"][0]["balance"] == "171.40"


@db
def test_without_a_model_configured_reading_is_unavailable(property_workspace, monkeypatch) -> None:
    def unavailable():
        raise ReaderUnavailable("reading documents needs OPENAI_API_KEY")

    monkeypatch.setattr(routes, "document_reader", unavailable)
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "umlage", "name": "A"}
    ).json()
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W3"}).json()
    file_id = client.post("/api/v1/files", params={"name": "a.txt"}, content=b"x 1,00").json()["id"]
    client.post(f"/api/v1/cases/{case['id']}/files", json={"file_id": file_id})

    res = client.post(f"/api/v1/cases/{case['id']}/extract")
    assert res.status_code == 503 and "OPENAI_API_KEY" in res.json()["detail"]
