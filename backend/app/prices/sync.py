"""Keep a workspace's website prices current, and hand them to a call (ADR-0032, Mia spec #6).

`refresh` reads the page and merges what it found into the cache; a read that fails, or finds no
prices, changes nothing but when it was tried and why it failed. `with_prices` puts the cache on
an agent's configuration at the start of a call, so the website, not the published snapshot, is
where a price comes from.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import web_import
from app.agents.config import AgentConfig, WebsitePrice
from app.db.models import PriceList
from app.prices.page import PriceRow, parse_prices

log = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 3600


def merged(rows: list[dict[str, Any]], found: list[PriceRow], now: datetime) -> list[dict[str, Any]]:
    """The cache after a successful read: every row found, as found now, then every row read
    before that the page no longer shows, unchanged. Nothing is ever dropped."""
    seen = now.isoformat()
    fresh = [{**row.public(), "seen_at": seen} for row in found]
    labels = {row["label"] for row in fresh}
    return fresh + [row for row in rows if row["label"] not in labels]


async def refresh(
    session: AsyncSession,
    price_list: PriceList,
    *,
    fetch: Callable[[str], str] | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    price_list.attempted_at = now
    try:
        html = await asyncio.to_thread(fetch or web_import.fetch, price_list.url)
        found = parse_prices(html)
        if not found:
            raise web_import.WebImportError("The page shows no prices any more.")
    except Exception as exc:  # any failure keeps the cache: that is the requirement
        log.warning("price refresh failed for %s: %s", price_list.tenant_id, exc)
        price_list.error = str(exc) or type(exc).__name__
    else:
        price_list.rows = merged(list(price_list.rows or []), found, now)
        price_list.checked_at = now
        price_list.error = ""
    await session.commit()


def with_prices(config: AgentConfig, price_list: PriceList | None) -> AgentConfig:
    """`config` with each linked treatment's price as the cache has it now. A row the last
    successful read did not find, or a cache whose last read failed, is marked stale: the agent
    then says it is the last known price. A treatment without a row gets no price at all."""
    rows = {r["label"]: r for r in (price_list.rows if price_list else [])}
    checked = price_list.checked_at.isoformat() if price_list and price_list.checked_at else None
    failing = bool(price_list and price_list.error)
    treatments = []
    for t in config.treatments:
        row = rows.get(t.price_label) if t.price_label else None
        price = None
        if row is not None:
            price = WebsitePrice(
                text=row["text"],
                amount=row["amount"],
                kind=row["kind"],
                detail=row.get("detail", ""),
                as_of=row["seen_at"][:10],
                stale=failing or row["seen_at"] != checked,
            )
        treatments.append(t.model_copy(update={"website_price": price}))
    return config.model_copy(update={"treatments": treatments})


async def run_price_refreshes(sessionmaker) -> None:
    """Read every workspace's price page once an hour, for as long as the process runs. A page
    that was down is simply read again next hour: the cache recovers by itself."""
    from app.db import repo

    while True:
        try:
            async with sessionmaker() as session:
                for price_list in await repo.price_lists(session):
                    await refresh(session, price_list)
        except Exception:  # the loop must outlive one bad hour
            log.exception("price refresh round failed")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
