"""Letters as PDF (ADR-0037): the platform renders any definition's letter, and the PDF says
exactly what the checked text says. Read back from the PDF itself, not from what was sent into
it. Run on the demo definition and on hand-written letters."""

from __future__ import annotations

import io
from datetime import date

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api.deps import current_tenant
from app.kernel.drafts import amounts_not_in
from app.kernel.modules import workflow
from app.kernel.workflows import Draft
from app.letters.pdf import letter_pdf
from app.main import app
from tests.test_workflow_agents import _as, _cleanup, _fixture, _PROPERTY, db

ON = date(2026, 10, 21)
SENDER = {"sender": "Hausverwaltung Muster GmbH", "sender_address": "Musterstraße 1, 90402 Nürnberg"}


def _letters(name: str, settings: dict):
    """The demo definition's letters for one case, written in the sandbox."""
    _, wf = workflow("umlage")
    computed = wf.compute(_fixture(name), settings)
    return wf.drafts(_fixture(name), settings, computed.result), computed.result


def _pages(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def test_a_letter_reads_as_its_text_with_letterhead_date_and_enclosure() -> None:
    letters, _ = _letters("u2", SENDER)
    adler = letters[0]
    pdf = letter_pdf(adler, ON)
    pages = _pages(pdf)

    assert pdf.startswith(b"%PDF")
    first = pages[0]
    assert "Hausverwaltung Muster GmbH" in first and "Musterstraße 1, 90402 Nürnberg" in first
    assert "Herr Adler" in first and "21.10.2026" in first
    assert adler.title in first.replace("\n", " ")
    assert "überweisen Sie 58,47 €" in " ".join(first.split())
    assert len(pages) >= 2 and "Anlage: Aufstellung Herr Adler" in pages[1]
    assert "Kosten 1.048,47 €" in "".join(pages[1:])


def test_every_amount_in_the_pdf_is_in_the_result() -> None:
    letters, result = _letters("u2", SENDER)
    for letter in letters:
        text = " ".join(" ".join(_pages(letter_pdf(letter, ON))).split())
        assert amounts_not_in(text, result) == [], letter.recipient


def test_german_characters_survive_and_long_enclosure_lines_wrap_not_cut() -> None:
    reason = (
        "nicht verrechnet: die Klausel zu „Schönheitsreparaturen“ ist unwirksam, "
        "weil die Wohnung unrenoviert übergeben wurde; die Abrechnung der Kaution ist Sache des "
        "Vermieters (BGH, Urt. v. 18.03.2015 – VIII ZR 185/14)"
    )
    letter = Draft("Kautionsabrechnung – Wohnung 2", "Frau Ähler", "Sehr geehrte Frau Ähler,\n\n"
                   "wir zahlen Ihnen 733,63 € aus.\n\nMit freundlichen Grüßen\n",
                   enclosure=f"Renovierung  450,00 €  {reason}\n", enclosure_name="Aufstellung")
    text = " ".join(" ".join(_pages(letter_pdf(letter, ON))).split())

    assert "„Schönheitsreparaturen“" in text and "Sehr geehrte Frau Ähler," in text
    assert "733,63 €" in text
    # The long enclosure line is wrapped onto further lines, not cut at the page edge.
    assert "VIII ZR 185/14" in text
    assert "die Abrechnung der Kaution ist Sache des Vermieters" in text


def test_a_letter_without_letterhead_or_enclosure_is_one_plain_page() -> None:
    pdf = letter_pdf(Draft("Betreff", "Frau X", "Guten Tag,\n\nText.\n\nMit freundlichen Grüßen\n"), ON)
    (page,) = _pages(pdf)
    assert "Frau X" in page and "Betreff" in page and "21.10.2026" in page


# --- the PDF of a case's letter, through the API ------------------------------------------

client = TestClient(app)


@db
def test_a_ready_cases_letter_downloads_as_pdf_and_nothing_else_does() -> None:
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "demo")
    try:
        agent = client.post(
            "/api/v1/workflow-agents", json={"workflow": "umlage", "name": "A"}
        ).json()
        client.put(
            f"/api/v1/workflow-agents/{agent['id']}/settings",
            json={"settings": SENDER},
        )
        case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W5"}).json()
        client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("u2")})
        assert client.get(f"/api/v1/cases/{case['id']}/letters/0/pdf").status_code == 404  # not computed
        client.post(f"/api/v1/cases/{case['id']}/compute")

        res = client.get(f"/api/v1/cases/{case['id']}/letters/1/pdf")
        assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
        assert res.headers["content-disposition"].startswith("attachment;")
        # The second letter: Frau Berg's, a refund.
        assert "erstatten Ihnen 196,39 €" in " ".join(" ".join(_pages(res.content)).split())
        assert client.get(f"/api/v1/cases/{case['id']}/letters/3/pdf").status_code == 404
    finally:
        app.dependency_overrides.pop(current_tenant, None)
        _cleanup()
