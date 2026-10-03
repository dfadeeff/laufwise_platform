"""A practice's website page becomes a document, and its price list becomes treatments to confirm.

ADR-0019. The fetch reaches only public http(s) addresses (never the platform's own network), is
capped in size and time, and nothing it proposes reaches an agent until a person saves it.
"""

from __future__ import annotations

import httpx
import pytest

# Shaped like healthyfeet-podologie.de/de/preise: cards of name, description, duration, price.
PRICE_PAGE = """<html><head><title>Preise | Healthy Feet</title><script>var x="99 €";</script></head>
<body><nav><a>Startseite</a><a>Preise</a></nav><main>
<h1>Unsere Preise</h1>
<div><p>Kassenleistungen</p><p>Podologische Behandlung mit Heilmittelverordnung</p>
<p>ca. 30 Min.</p><span>0 €</span></div>
<h2>Privatleistungen</h2>
<div><p class="font-medium">Medizinische Fußpflege</p><p>Komplette podologische Behandlung</p>
<p>ca. 30 Min.</p><span>69 €</span></div>
<div><p class="font-medium">Hornhaut entfernen</p><p>Gezielte, schonende Abtragung</p>
<p>ca. 30 Min.</p><span>55 €</span></div>
<h2>Beratung &amp; Kontrolle</h2>
<div><p class="font-medium">Erstberatung</p><p>Befundung beider Füße</p><span>25 €</span></div>
<div><p>Kaltplasma – 6 Sitzungen</p><p>Paketpreis · 5% Rabatt</p><p>ca. 30 Min. pro Sitzung</p>
<span>375 €</span></div>
<p>Alle Preise in Euro inkl. MwSt.</p>
</main><footer>Impressum · Datenschutz</footer></body></html>"""


def test_a_page_s_readable_text_is_kept_and_its_navigation_and_scripts_are_not() -> None:
    from app.agents.web_import import page_text

    title, text = page_text(PRICE_PAGE)

    assert title == "Preise | Healthy Feet"
    assert "Hornhaut entfernen" in text and "Alle Preise in Euro" in text
    assert "Startseite" not in text and "Impressum" not in text and "var x" not in text


def test_a_price_list_becomes_treatments_named_as_the_practice_names_them() -> None:
    from app.agents.web_import import price_proposals

    proposed = {p["name"]: p["price_eur"] for p in price_proposals(PRICE_PAGE)}

    assert proposed == {
        "Medizinische Fußpflege": 69,
        "Hornhaut entfernen": 55,
        "Erstberatung": 25,
        "Kaltplasma – 6 Sitzungen": 375,
    }


def test_proposed_treatments_get_keys_a_booking_can_use() -> None:
    from app.agents.web_import import price_proposals

    keys = [p["key"] for p in price_proposals(PRICE_PAGE)]

    assert "medizinische_fusspflege" in keys and "kaltplasma_6_sitzungen" in keys
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/preise",
        "http://localhost/admin",
        "http://127.0.0.1:8080/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
def test_only_public_web_pages_can_be_fetched(url) -> None:
    """The fetch runs inside the platform: a URL pointing inward would read what it must not."""
    from app.agents.web_import import WebImportError, fetch

    with pytest.raises(WebImportError):
        fetch(url)


def test_a_redirect_into_the_private_network_is_refused(monkeypatch) -> None:
    from app.agents import web_import

    monkeypatch.setattr(web_import, "_public", lambda host: host == "practice.example")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.local/secrets"})

    with pytest.raises(web_import.WebImportError, match="not a public"):
        web_import.fetch("https://practice.example/preise", transport=httpx.MockTransport(handler))


def test_a_public_page_is_fetched(monkeypatch) -> None:
    from app.agents import web_import

    monkeypatch.setattr(web_import, "_public", lambda host: True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=PRICE_PAGE, headers={"content-type": "text/html; charset=utf-8"}
        )
    )

    assert "Hornhaut" in web_import.fetch("https://practice.example/preise", transport=transport)


def test_something_that_is_not_a_web_page_is_refused(monkeypatch) -> None:
    from app.agents import web_import

    monkeypatch.setattr(web_import, "_public", lambda host: True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    )

    with pytest.raises(web_import.WebImportError, match="web page"):
        web_import.fetch("https://practice.example/file", transport=transport)


def test_the_studio_is_offered_a_price_page_s_treatments_and_nothing_is_saved(monkeypatch) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import current_tenant
    from app.api.v1 import knowledge

    monkeypatch.setattr(knowledge.web_import, "fetch", lambda url: PRICE_PAGE)
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/knowledge")
    app.dependency_overrides[current_tenant] = lambda: SimpleNamespace(id="t")

    offered = TestClient(app).post("/knowledge/prices", json={"url": "https://practice.example/preise"})

    assert offered.status_code == 200
    assert {"key": "hornhaut_entfernen", "name": "Hornhaut entfernen", "price_eur": 55} in offered.json()["treatments"]


def test_a_page_with_no_prices_says_so(monkeypatch) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import current_tenant
    from app.api.v1 import knowledge

    monkeypatch.setattr(knowledge.web_import, "fetch", lambda url: "<main><p>Willkommen</p></main>")
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/knowledge")
    app.dependency_overrides[current_tenant] = lambda: SimpleNamespace(id="t")

    refused = TestClient(app).post("/knowledge/prices", json={"url": "https://practice.example/"})

    assert refused.status_code == 422 and "No prices" in refused.json()["detail"]
