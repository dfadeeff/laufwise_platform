"""The Betriebskosten letters (ADR-0034), as the platform writes them: the definition's letters
function, in the sandbox. One per tenant, none for a vacant month, at most five sentences, with
the balance and the deadline; a request to whoever must supply a missing fact."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from app.kernel.drafts import amounts_not_in
from app.kernel.modules import workflow
from app.kernel.schema import empty
from app.kernel.workflows import Draft

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = {"basis": "unit_share", "sender": "Hausverwaltung Muster", "payment_days": 30}


def _workflow():
    return workflow("betriebskosten")[1]


def _letters(name: str, settings: dict = SETTINGS) -> list[Draft]:
    raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))
    computed = _workflow().compute(raw, settings)
    return _workflow().drafts(raw, settings, computed.result)


def _sentences(letter: Draft) -> list[str]:
    """The body's sentences, between the salutation and the closing. A sentence starts with a
    capital letter, so "§ 560 Abs. 4" is not two."""
    body = letter.text.split("\n\n")[1]
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", body.strip()) if s]


def test_nk01_one_letter_with_the_balance_the_deadline_and_the_new_advance() -> None:
    (letter,) = _letters("nk01")

    assert letter.recipient == "Familie Brettschneider"
    assert letter.text.startswith("Sehr geehrte Familie Brettschneider,")
    assert "Nachzahlung von 171,40 €" in letter.text
    assert "innerhalb von 30 Tagen" in letter.text
    assert "von 180,00 € auf 194,28 €" in letter.text
    assert letter.text.rstrip().endswith("Hausverwaltung Muster")
    assert len(_sentences(letter)) <= 5


def test_nk02_a_letter_per_tenant_none_for_the_vacant_month_and_a_request_for_what_is_open() -> None:
    oezdemir, lindqvist, messdienst = _letters("nk02")

    assert (oezdemir.recipient, lindqvist.recipient) == ("Herr Özdemir", "Frau Lindqvist")
    assert oezdemir.text.startswith("Sehr geehrter Herr Özdemir,")
    assert "Nachzahlung von 58,47 €" in oezdemir.text
    assert "Vorauszahlung" not in oezdemir.text.split("\n\n")[1].split("Daraus")[1]  # no proposal
    # The new tenant's heating share waits on the Messdienst: no balance, but the date the
    # complete settlement arrives by, and what is missing from whom.
    assert lindqvist.text.startswith("Sehr geehrte Frau Lindqvist,")
    assert "spätestens bis zum 31.12.2026" in lindqvist.text
    assert "bis zum 01.12.2026 angefordert" in lindqvist.text
    assert "Guthaben" not in lindqvist.text and "Nachzahlung" not in lindqvist.text
    assert messdienst.recipient == "Messdienst"
    assert "113,82 €" in messdienst.text and "bis zum 01.12.2026" in messdienst.text
    assert "Gradtagszahlen" in messdienst.text
    assert all(len(_sentences(letter)) <= 5 for letter in (oezdemir, lindqvist, messdienst))


def test_every_amount_in_the_letters_is_in_the_result() -> None:
    for name in ("nk01", "nk02"):
        raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str))
        computed = _workflow().compute(raw, SETTINGS)
        for letter in _workflow().drafts(raw, SETTINGS, computed.result):
            assert amounts_not_in(letter.text, computed.result) == []


def test_a_new_agent_starts_with_the_settings_defaults() -> None:
    settings = empty(_workflow().schemas()["settings"])
    assert settings == {"basis": None, "sender": "", "sender_address": "", "payment_days": 30}


def test_an_agent_from_before_the_letter_settings_still_computes() -> None:
    (letter,) = _letters("nk01", {"basis": "unit_share"})
    assert "innerhalb von 30 Tagen" in letter.text
    assert "Mit freundlichen Grüßen" in letter.text


def test_each_letter_carries_the_statement_it_says_is_enclosed() -> None:
    oezdemir, lindqvist, _ = _letters("nk02")

    assert oezdemir.enclosure_name == "Einzelaufstellung Herr Özdemir"
    assert "Herr Özdemir" in oezdemir.enclosure
    assert "01.01.2025 – 30.06.2025" in oezdemir.enclosure
    assert "Heizung und Warmwasser" in oezdemir.enclosure and "360,42 €" in oezdemir.enclosure
    assert "Saldo (Nachzahlung)" in oezdemir.enclosure and "58,47 €" in oezdemir.enclosure
    assert "§ 556 Abs. 3 BGB: 31.12.2026" in oezdemir.enclosure
    assert "Miete der Rauchwarnmelder" in oezdemir.enclosure  # what was not passed on, and why
    assert "BGH, Urt. v. 11.05.2022" in oezdemir.enclosure
    assert "Summe der feststehenden Positionen" in lindqvist.enclosure
    assert "Noch zu verteilen" in lindqvist.enclosure and "113,82 €" in lindqvist.enclosure


def test_the_enclosures_amounts_are_in_the_result_too() -> None:
    raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / "nk02.yaml").read_text()), default=str))
    computed = _workflow().compute(raw, SETTINGS)
    for letter in _workflow().drafts(raw, SETTINGS, computed.result):
        assert amounts_not_in(letter.enclosure, computed.result) == []




def test_every_amount_in_the_pdf_is_in_the_result_and_the_enclosure_itemises() -> None:
    import io
    from datetime import date

    from pypdf import PdfReader

    from app.letters.pdf import letter_pdf

    raw = json.loads(json.dumps(yaml.safe_load((FIXTURES / "nk02.yaml").read_text()), default=str))
    computed = _workflow().compute(raw, SETTINGS)
    for letter in _workflow().drafts(raw, SETTINGS, computed.result):
        pages = [p.extract_text() for p in PdfReader(io.BytesIO(letter_pdf(letter, date(2026, 10, 21)))).pages]
        text = " ".join(" ".join(pages).split())
        assert amounts_not_in(text, computed.result) == [], letter.recipient
        if letter.recipient == "Herr Özdemir":
            assert "Heizung und Warmwasser" in text and "Nachzahlung von 58,47 €" in text


def test_a_citation_a_tenant_reads_names_a_norm_never_our_own_review() -> None:
    # Seen in NK-03: a Wartung held back printed "Fachprüfung NK-02" in the tenant's enclosure.
    # Where a rule comes from internally belongs in a comment beside it, not in its citation.
    catalogue = yaml.safe_load((Path(__file__).parents[1] / "rules" / "catalogue.yaml").read_text())
    internal = re.compile(r"Fachprüfung|Lessons|NK-\d|Test \d")
    leaks = {k: c["citation"] for k, c in catalogue["categories"].items() if internal.search(c.get("citation", ""))}
    assert leaks == {}
