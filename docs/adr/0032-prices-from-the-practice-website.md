# ADR-0032 — Prices come from the practice website, read by the platform, quoted by a tool

Status: accepted · 20 October 2026 · implements #133 (Mia specification v3, item 6) · builds on
ADR-0025 (service catalogue), ADR-0019 (import from the practice website), ADR-0017 (knowledge)

## Context

The specification makes the practice website (`healthyfeet-podologie.de/de/preise`, München) the
primary price source: never hard-coded in a prompt, refreshed automatically, mapped to services,
"ab" and package prices handled, and when the website is down the last successfully read price is
still quoted — with its date, and as the last known one. A service that never had a verified price
gets no figure at all.

Until now a price reached the agent only as text in a knowledge document imported once from that
page (ADR-0019), or as `price_eur` on a treatment that the service catalogue (ADR-0025) never
fills. Nothing was dated, refreshed or tied to a service.

## Decision

### D1 — One price page per workspace, read by the platform

`price_list` (one row per workspace): the page URL and every price row ever read there, each with
`seen_at`; plus `checked_at` (last read that found prices), `attempted_at` and `error`. The page is
read when the practice saves its URL, on "Check now", and every hour by a loop beside the app
(like the retention sweep). The fetch is ADR-0019's (`web_import.fetch`: public addresses only,
redirects re-checked, 2 MB cap).

A read that fails — the site is down, or the page shows no prices — changes only `attempted_at`
and `error`. A successful read updates the rows it found and keeps every other row as it was. So
a cached price is never deleted, and the next successful read repairs the cache by itself.

### D2 — The page is parsed deterministically, by structure

`app/prices/page.py`: a price is a text like `55 €`, `ab 69 €`, `0 €*`; its row is the smallest
element around it that holds other text and no other price; the row's first text is its name.
`ab` makes the price a starting price; a row that says "Paket" makes it a package price. No
model, nothing specific to one site's markup: a table row, a list item or a card read the same.
The Healthy Feet page reads as its 20 rows (`tests/fixtures/prices/`).

### D3 — A service names its row; the price is read per call, never pinned

`workspace_service.price_label` names the service's row on the page, chosen in Studio → Services
from the rows read. The label is catalogue data and is pinned at publish like the rest of the
service (ADR-0025). The **price** is not: at the start of every call, `open_voice_call` puts the
workspace's current rows on the configuration (`with_prices`). A price change on the website
therefore needs neither a prompt edit nor a republish.

A price is **stale** when the last read failed, or the last successful read no longer found its
row. A linked service whose row was never read has no price.

### D4 — The agent quotes a price only through `get_service_price`

The prompt carries no figure for a website-priced service; it says to call `get_service_price`
for every price and never quote one from memory or the documents (the documents may hold an old
copy of the page). The tool returns the price as written, its kind and date, and `agent_notes`:
the price as it stands; a starting price or package said as such; a stale price said as "Nach
unserer zuletzt verfügbaren Preisliste kostet …"; no price → no figure, the practice confirms it.
The tool is offered only to an agent with at least one linked service (`capabilities.resolve`), in
`practice_info` and `book_appointment`.

## Consequences

- Healthy Feet: Studio → Services → "Your price page" = `https://www.healthyfeet-podologie.de/de/preise`,
  then link each service to its row, then publish (the links are pinned; prices are not).
- An agent with no linked service keeps its old behaviour: the price list in the prompt.
- Only the prices in the page's HTML are read: the München list. Augsburg is filled in by the
  browser when its tab is clicked; a second location needs its own page or a location parameter
  (`location_id` in the specification) when it is wanted.
- The hourly loop runs in every web process; with several replicas the page is read once per
  replica per hour, which is harmless and idempotent.
- The bundled eval agent links no prices, so the nightly voice replay does not exercise the tool;
  the behaviour is covered by unit tests against the session and a Postgres test of the API.
