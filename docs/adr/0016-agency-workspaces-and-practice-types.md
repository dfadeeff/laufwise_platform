# ADR-0016 — An agency is a login with many workspaces; a practice starts from its type

Status: accepted · 3 October 2026 · builds on ADR-0003 D2 (org = tenant)

## Context

The platform's first customers are small practices and the agencies that set them up. An agency
runs many practices. Two things were missing for it:

- **An overview.** One login could already belong to several Clerk organizations and switch
  between them (org = tenant, ADR-0003 D2), but the only way to see whether a client's phone line
  works was to switch into that workspace and look.
- **A starting point.** Every new agent started blank. A practice of a known kind (podiatry,
  physiotherapy, a dental or GP practice) re-entered the same treatments, hours and conversation
  style as every other practice of that kind.

## Decision

### D1 — No agency entity: the overview reads each workspace with that workspace's own token

An agency is not a new record. It is a login that belongs to several organizations. The Studio's
"All workspaces" page lists the login's memberships (Clerk), asks Clerk for a token scoped to each
(`getToken({ organizationId })`, which does not change the active organization), and calls
`GET /workspace/summary` with it. The backend trusts only the signed token, exactly as for every
other route. The overview therefore needs no new authorization rule, and a login can never see a
workspace it is not a member of.

The summary is one practice at a glance: agents (published, answering calls, number), calendar
connections, numbers, calls in the last seven days, callbacks waiting, and `attention`, the steps
still between the practice and a working phone line, in words the agency can act on.

A new client is a new organization, created from the same page; it opens straight into the new
workspace, where the empty agent list is the setup path.

### D2 — Practice types are data: one JSON file per type

`app/agents/practice_types/<key>.json` holds what practices of one kind share: usual treatments,
appointment length, opening hours and break, and the conversation-style instructions for that kind
(urgent pain goes to a callback in a dental practice; 112 for an emergency in a GP practice).
Creating an agent with `practice_type` fills its draft from the file. The practice then adds what
only it knows (name, address, phone, recipients, prices) and publishes through the same gate. A
test holds every type to that gate. Adding a type is adding a file.

These are called practice types, not templates, because "template" already means a governed
runbook (`app/templates`, the `template` table).

### D3 — A price nobody entered is "on request", never €0

Practice types cannot know a practice's prices, so they ship 0. The Studio's own default was 0
already. Read out, that is "€0", a promise the practice never made. A price of 0 is now rendered as
"price on request — the practice will confirm it", for every agent.

## Consequences

- An agency sees every client's state on one page and opens the one that needs work. Membership
  and roles stay in Clerk.
- The overview makes one request per workspace. Fine for tens of clients; hundreds would want a
  server-side aggregate, which would need its own membership check against Clerk.
- No white-labelling, per-client billing or agency-level roles: each workspace is managed as before.

## Open questions

- Which further practice types to ship (psychotherapy, dermatology, beauty), and whether a
  practice type should also preselect capabilities.
- Agency-wide reporting (calls across all clients) once one request per workspace is too slow.
