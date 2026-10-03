# ADR-0019 — Import from the practice's website: pages into documents, prices into treatments

Status: accepted · 3 October 2026 · extends ADR-0017 (workspace knowledge) · **amended 3 October 2026:** the treatment list and price proposals were removed from the Studio. Services and prices now live only in documents, and an agent without a treatment list books a plain appointment (`GENERIC_APPOINTMENT`). D1's second bullet and D2 describe the removed feature.

## Context

A practice's prices and FAQ are usually already on its website. Retyping them into the Studio is
slow and error-prone; a practice asked to point at its price page instead.

## Decision

### D1 — A page becomes a document; a price list becomes treatment proposals

- `POST /knowledge/url` fetches a page, keeps its readable text (the `<main>` element if it has
  one; navigation, header, footer, scripts and forms dropped) and saves it as a workspace document
  with `source = "url"` and a `Source:` line. It is then reviewed, edited and pinned like any other
  document.
- `POST /knowledge/prices` returns treatment proposals (`key`, `name`, `price_eur`) and saves
  nothing. The agent's Treatments section shows them with checkboxes; ticked rows are added, or
  update a treatment with the same key, and reach callers only when the agent is saved and
  published. Prices stay structured and confirmed by a person, never paraphrased from text.

Reading uses the standard library's HTML parser; no new dependency.

### D2 — How a price list is read

A price on its own line belongs to the card above it: the first line in that card that is not a
section heading or a duration ("ca. 30 Min.") is its name. A price written inline ("Erstberatung
25 €") is taken from the same line. A price of 0 is left out: insurance-covered treatments are
listed at 0, and 0 already reads as "price on request". Checked against the Healthy Feet price page,
where all 15 treatments come out with their names and prices.

### D3 — The fetch can only reach the public web

A URL makes the platform send a request, so `fetch` accepts only http(s) on the default ports, and
every hostname, including each redirect's, must resolve to globally routable addresses only. That
keeps out localhost, the private network and cloud metadata endpoints. Redirects are followed by
hand (at most five), each re-checked. Responses are capped at 2 MB and 10 seconds, and must be HTML
or plain text. A DNS answer that changes between the check and the connection is not defended
against; such a request is one GET whose body is only read back as text.

## Consequences

- A practice fills its Treatments and its knowledge from its own site in a minute, and confirms
  what it takes over.
- Pages that render their content with JavaScript only yield what the server sends; such a page
  imports little or nothing, and the practice pastes the text instead.
