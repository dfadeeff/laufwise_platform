"""Prices come from the practice website, stay quotable when it is down, and are never invented
(ADR-0032, Mia spec #6, issue #133).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.config import AgentConfig
from app.agents.knowledge import prompt_block
from app.prices.page import parse_prices
from app.prices.sync import merged, refresh, with_prices
from app.workloads.conversational import capabilities
from app.workloads.conversational.booking import BookingSession
from tests.test_agent_workspace_db import URL, complete_config, workspace  # noqa: F401 — fixture

PAGE = (Path(__file__).parent / "fixtures" / "prices" / "healthyfeet-muenchen-2026-10-20.html").read_text(
    encoding="utf-8"
)
_IDENTITY = dict(
    practice_name="Podologie Healthy Feet München",
    street="Baumkirchner Straße 19",
    postcode="81673",
    city="München",
    phone="+498941115335",
    recipients=["team@example.org"],
    consent_policy_id="policy-v1",
)
_TREATMENTS = [
    {"key": "gross", "name": "Medizinische Fußpflege groß", "external_id": "12937",
     "price_label": "Medizinische Fußpflege – groß"},
    {"key": "klebespange", "name": "Klebespange", "price_label": "Klebespange"},
    {"key": "kaltplasma_6", "name": "Kaltplasma 6", "price_label": "Kaltplasma – 6 Sitzungen"},
    {"key": "beratung", "name": "Erstberatung", "external_id": "380625"},  # no website row
]
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _runs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workloads.conversational.booking.settings.runs_dir", str(tmp_path))


# --- the page ------------------------------------------------------------------------------------


def test_every_price_on_the_practice_page_is_read_with_its_own_name() -> None:
    rows = {r.label: r for r in parse_prices(PAGE)}

    assert len(rows) == 20
    assert rows["Medizinische Fußpflege – groß"].amount == Decimal("69")
    assert rows["Klebespange"].kind == "from" and rows["Klebespange"].text == "ab 69 €"
    assert rows["Kaltplasma – 12 Sitzungen"].kind == "package"
    # A section with a single price is not named after its heading.
    assert "Podologische Behandlung mit Heilmittelverordnung (Rezept)" in rows
    assert "Kassenleistungen" not in rows


def test_a_table_is_read_as_well_as_cards() -> None:
    html = (
        "<table><tr><th>Leistung</th><th>Preis</th></tr>"
        "<tr><td>Hornhaut entfernen</td><td>55,00 €</td></tr>"
        "<tr><td>Warze</td><td>ab 40 EUR</td></tr></table>"
    )

    rows = parse_prices(html)

    assert [(r.label, r.amount, r.kind) for r in rows] == [
        ("Hornhaut entfernen", Decimal("55.00"), "fixed"),
        ("Warze", Decimal("40"), "from"),
    ]


def test_a_page_without_prices_yields_nothing_rather_than_a_guess() -> None:
    assert parse_prices("<html><body><p>Wir sind umgezogen.</p></body></html>") == []


# --- the cache -----------------------------------------------------------------------------------


class _Session:
    async def commit(self) -> None:
        pass


def _list(**kw):
    base = dict(tenant_id="t", url="https://example.org/preise", rows=[], checked_at=None,
                attempted_at=None, error="")
    return SimpleNamespace(**{**base, **kw})


def _refreshed(price_list, fetch, now):
    asyncio.run(refresh(_Session(), price_list, fetch=fetch, now=now))
    return price_list


def test_a_successful_read_stores_every_price_with_its_time() -> None:
    price_list = _refreshed(_list(), lambda url: PAGE, T0)

    assert price_list.checked_at == T0 and price_list.error == ""
    assert len(price_list.rows) == 20
    assert {r["seen_at"] for r in price_list.rows} == {T0.isoformat()}


@pytest.mark.parametrize(
    "fetch",
    [
        lambda url: (_ for _ in ()).throw(TimeoutError("timed out")),
        lambda url: "<html><body>Wartungsarbeiten</body></html>",
    ],
    ids=["website-down", "page-without-prices"],
)
def test_a_failed_read_keeps_every_cached_price(fetch) -> None:
    price_list = _refreshed(_list(), lambda url: PAGE, T0)
    before = list(price_list.rows)

    _refreshed(price_list, fetch, T0 + timedelta(hours=1))

    assert price_list.rows == before
    assert price_list.checked_at == T0
    assert price_list.attempted_at == T0 + timedelta(hours=1)
    assert price_list.error


def test_a_row_that_leaves_the_page_keeps_its_last_price() -> None:
    first = parse_prices(PAGE)
    rows = merged([], first, T0)

    later = merged(rows, [r for r in first if r.label != "Klebespange"], T0 + timedelta(hours=1))

    kept = next(r for r in later if r["label"] == "Klebespange")
    assert kept["text"] == "ab 69 €" and kept["seen_at"] == T0.isoformat()


def test_the_cache_recovers_by_itself_when_the_page_is_back() -> None:
    price_list = _refreshed(_list(), lambda url: PAGE, T0)
    _refreshed(price_list, lambda url: (_ for _ in ()).throw(OSError("down")), T0 + timedelta(hours=1))

    _refreshed(price_list, lambda url: PAGE, T0 + timedelta(hours=2))

    assert price_list.error == "" and price_list.checked_at == T0 + timedelta(hours=2)


# --- a call --------------------------------------------------------------------------------------


def _config() -> AgentConfig:
    return AgentConfig(**_IDENTITY, treatments=_TREATMENTS)


def _call(price_list) -> BookingSession:
    return BookingSession("prices", practice=with_prices(_config(), price_list).to_practice())


def _cached(**kw):
    return _refreshed(_list(), lambda url: PAGE, T0) if not kw else _list(**kw)


def test_a_linked_service_is_quoted_as_the_website_shows_it() -> None:
    result = _call(_cached()).get_service_price("gross")

    assert result["price"] == "69 €" and result["stale"] is False
    assert result["as_of"] == "2026-10-20"
    assert "69 €" in result["agent_notes"][0]


def test_a_starting_price_and_a_package_say_what_they_are() -> None:
    call = _call(_cached())

    assert "starting price" in " ".join(call.get_service_price("klebespange")["agent_notes"])
    assert "package price" in " ".join(call.get_service_price("kaltplasma_6")["agent_notes"])


def test_when_the_website_is_down_the_last_price_is_said_as_the_last_known_one() -> None:
    price_list = _cached()
    _refreshed(price_list, lambda url: (_ for _ in ()).throw(OSError("down")), T0 + timedelta(hours=1))

    result = _call(price_list).get_service_price("gross")

    assert result["price"] == "69 €" and result["stale"] is True
    assert "Nach unserer zuletzt verfügbaren Preisliste" in result["agent_notes"][0]


def test_a_service_with_no_verified_price_gets_no_figure() -> None:
    result = _call(_cached()).get_service_price("beratung")

    assert result["price"] is None
    assert "Do not estimate" in result["agent_notes"][0]


def test_a_practice_that_never_read_its_page_has_no_prices_at_all() -> None:
    assert _call(None).get_service_price("gross")["price"] is None


def test_no_price_figure_is_in_the_prompt_and_the_documents_are_not_a_source() -> None:
    block = with_prices(_config(), _cached()).to_practice().knowledge_block()

    assert "69" not in block
    assert "call get_service_price for every price" in block
    assert "Never quote a price from these documents" in prompt_block(
        [{"title": "Preise", "content": "Fußpflege 45 €"}], prices_by_tool=True
    )


def test_the_tool_is_offered_only_where_a_price_is_linked() -> None:
    assert "get_service_price" in capabilities.resolve(_config()).tools
    unlinked = AgentConfig(
        **_IDENTITY, treatments=[{k: v for k, v in t.items() if k != "price_label"} for t in _TREATMENTS]
    )
    assert "get_service_price" not in capabilities.resolve(unlinked).tools


# --- the Studio and a published agent, against Postgres --------------------------------------------


@pytest.mark.skipif(not URL, reason="requires STUDIO_TEST_DATABASE_URL")
def test_a_workspace_names_its_page_links_a_service_and_a_call_reads_todays_price(
    workspace, monkeypatch  # noqa: F811
) -> None:
    client, owner, _, _, maker = workspace
    pages = {"now": PAGE}
    monkeypatch.setattr("app.agents.web_import.fetch", lambda url: pages["now"])

    read = client.put("/api/v1/prices", json={"url": "https://www.example.org/de/preise"})
    assert read.status_code == 200, read.text
    assert len(read.json()["rows"]) == 20 and read.json()["error"] == ""

    client.post(
        "/api/v1/services",
        json={"name": "Fußpflege groß", "external_id": "12937",
              "price_label": "Medizinische Fußpflege – groß"},
    ).raise_for_status()
    agent = client.post("/api/v1/agents").json()
    path = "/api/v1/agents/" + agent["id"]
    client.post(path + "/draft", json={"generation": 1, "config": complete_config()}).raise_for_status()
    client.post(path + "/publish", json={"generation": 2}).raise_for_status()
    pinned = client.get(path).json()["history"][0]["config"]
    assert pinned["treatments"][0]["price_label"] == "Medizinische Fußpflege – groß"
    assert pinned["treatments"][0]["website_price"] is None  # never pinned: read per call

    # The website changes its price: no republish, the next call says the new one.
    pages["now"] = PAGE.replace(">69 €<", ">72 €<")
    assert client.post("/api/v1/prices/refresh").status_code == 200

    async def price_list():
        from app.db import repo

        async with maker() as s:
            return await repo.get_price_list(s, owner.id)

    config = with_prices(AgentConfig.model_validate(
        {k: v for k, v in pinned.items() if k not in ("contracts", "base_prompt", "knowledge")}
    ), asyncio.run(price_list()))
    call = BookingSession("db-prices", practice=config.to_practice())
    assert call.get_service_price(config.treatments[0].key)["price"] == "72 €"

    # The page goes down: the price stays, said as the last known one.
    pages["now"] = "<html><body>503</body></html>"
    after = client.post("/api/v1/prices/refresh").json()
    assert after["error"] and len(after["rows"]) == 20
