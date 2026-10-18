# ADR-0025 — Services are workspace data, and only linked services are booked by phone

Status: accepted · 18 October 2026 · implements #130 (Mia specification v3) · amends ADR-0019
(amended: "no treatment list") · builds on ADR-0017 (workspace data pinned at publish), ADR-0022

## Context

On 3 October the treatment list was removed and agents booked one plain appointment. The Mia
specification v3 reverses that for good reasons: a booking must name the treatment, must never
fall back to a generic one because the caller was unsure, and must be linked to the calendar
system's own service id. It defines a release gate per service: no automatic booking until its id
(and duration) are verified.

What Thevea actually holds (read-only recon of the practice account, 18 October 2026): one
appointment category ("Privat"), the national statutory catalogue (Heilmittel), and a private
price list (`privatPreisliste`, id 1462) of 11 positions with ids and prices — no durations, and
not the ~22 services the website lists. So the mapping cannot be imported blindly; the practice
has to decide it, service by service.

## Decision

### D1 — A catalogue per workspace

`workspace_service`: name, a stable key (from the name), the calendar system's own id
(`external_id`; in Thevea the price-list position), duration as verified by the practice, how
callers say it per language (de/en/ru — used for recognition by #131), `bookable`, and
`consultation` (offered when a caller cannot say what they need). Edited in Studio → Knowledge
base → Services, shared by all agents of the workspace. Not Healthy Feet code: any practice fills
its own.

### D2 — Pinned at publish, like documents

`make_snapshot` copies the catalogue into the agent's `treatments`; a published agent books what
it pinned, never what the catalogue says today. A workspace without a catalogue keeps the plain
appointment (`GENERIC_APPOINTMENT`) — other practices are unaffected.

### D3 — The release gate is the backend's

A service is bookable by phone only if `bookable` AND linked (`external_id` set). Only those reach
the booking tool's `service_key` enum; anything else is rejected with "not set up for booking by
phone yet … take a callback request". With several services nothing is pre-selected. An unknown
treatment is refused without suggesting a default. The appointment's note names the service and
its id ("… (Leistung 12937)").

### D4 — No generic fallback in the prompt

The booking skill's "if unsure, book the standard medical foot treatment" is gone. The agent
asks one short question, then offers the consultation if one is marked, otherwise a callback. It
gets the catalogue with each service's aliases and status.

## Consequences

- Healthy Feet must fill its catalogue before Mia books anything specific; until a service is
  linked, callers asking for it get a callback (by design).
- Durations are recorded but appointments still use the grid's slot length — enforcing them is
  #140.
- Payment category (#132), synonym/ambiguity rules (#131) and backend-required service id in the
  contract (#142) build on this.
