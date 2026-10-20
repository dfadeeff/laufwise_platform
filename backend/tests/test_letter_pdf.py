"""Letters as PDF (ADR-0037): the kernel renders any workflow's letter, and the PDF says exactly
what the checked text says. Read back from the PDF itself, not from what was sent into it."""

from __future__ import annotations

import io
import json
from datetime import date

import yaml
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api.deps import current_tenant
from app.kernel.drafts import amounts_not_in
from app.kernel.modules import workflow
from app.letters.pdf import letter_pdf
from app.main import app
from tests.test_betriebskosten import FIXTURES as NK
from tests.test_kaution import FIXTURES as KA
from tests.test_workflow_agents import _as, _cleanup, _fixture, _PROPERTY, db

ON = date(2026, 10, 21)
SENDER = {"sender": "Hausverwaltung Muster GmbH", "sender_address": "Musterstraße 1, 90402 Nürnberg"}


def _letters(key: str, fixtures, name: str, settings: dict):
    _, wf = workflow(key)
    raw = json.loads(json.dumps(yaml.safe_load((fixtures / f"{name}.yaml").read_text()), default=str))
    parsed = wf.parse(raw, settings)
    computed = wf.compute(parsed)
    return [d for d in wf.drafts(parsed, computed.value)], computed.result


def _pages(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def test_a_letter_reads_as_its_text_with_letterhead_date_and_enclosure() -> None:
    letters, _ = _letters("betriebskosten", NK, "nk02", {"basis": "unit_share", **SENDER})
    oezdemir = letters[0]
    pdf = letter_pdf(oezdemir, ON)
    pages = _pages(pdf)

    assert pdf.startswith(b"%PDF")
    first = pages[0]
    assert "Hausverwaltung Muster GmbH" in first and "Musterstraße 1, 90402 Nürnberg" in first
    assert "Herr Özdemir" in first and "21.10.2026" in first
    assert oezdemir.title in first.replace("\n", " ")
    assert "Nachzahlung von 42,61 €" in " ".join(first.split())
    assert len(pages) >= 2 and "Anlage: Einzelaufstellung Herr Özdemir" in pages[1]
    assert "Heizung und Warmwasser" in "".join(pages[1:])


def test_every_amount_in_the_pdf_is_in_the_result() -> None:
    for key, fixtures, name, settings in (
        ("betriebskosten", NK, "nk02", {"basis": "unit_share", **SENDER}),
        ("kaution", KA, "ka02", SENDER),
    ):
        letters, result = _letters(key, fixtures, name, settings)
        for letter in letters:
            text = " ".join(" ".join(_pages(letter_pdf(letter, ON))).split())
            assert amounts_not_in(text, result) == [], (name, letter.recipient)


def test_german_characters_survive_and_long_enclosure_lines_wrap_not_cut() -> None:
    (letter,), _ = _letters("kaution", KA, "ka02", SENDER)
    text = " ".join(" ".join(_pages(letter_pdf(letter, ON))).split())

    assert "„" not in letter.text or "„" in text
    assert "733,63 €" in text and "Sehr geehrte Frau Albers," in text
    # The longest enclosure line (a reason and its citation) is wrapped onto further lines,
    # not cut at the page edge: its last words are there.
    assert "VIII ZR 185/14" in text
    assert "die Abrechnung der Kaution ist Sache des Vermieters" in text


def test_a_letter_without_letterhead_or_enclosure_is_one_plain_page() -> None:
    from app.kernel.workflows import Draft

    pdf = letter_pdf(Draft("Betreff", "Frau X", "Guten Tag,\n\nText.\n\nMit freundlichen Grüßen\n"), ON)
    (page,) = _pages(pdf)
    assert "Frau X" in page and "Betreff" in page and "21.10.2026" in page


# --- the PDF of a case's letter, through the API ------------------------------------------

client = TestClient(app)


@db
def test_a_ready_cases_letter_downloads_as_pdf_and_nothing_else_does() -> None:
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "immobilien")
    try:
        agent = client.post(
            "/api/v1/workflow-agents", json={"workflow": "betriebskosten", "name": "A"}
        ).json()
        client.put(
            f"/api/v1/workflow-agents/{agent['id']}/settings",
            json={"settings": {"basis": "unit_share", **SENDER}},
        )
        case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W5"}).json()
        client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("nk02")})
        assert client.get(f"/api/v1/cases/{case['id']}/letters/0/pdf").status_code == 404  # not computed
        client.post(f"/api/v1/cases/{case['id']}/compute")

        res = client.get(f"/api/v1/cases/{case['id']}/letters/1/pdf")
        assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
        assert res.headers["content-disposition"].startswith("attachment;")
        assert "Guthaben von 196,39 €" in " ".join(" ".join(_pages(res.content)).split())
        assert client.get(f"/api/v1/cases/{case['id']}/letters/2/pdf").status_code == 404
    finally:
        app.dependency_overrides.pop(current_tenant, None)
        _cleanup()
