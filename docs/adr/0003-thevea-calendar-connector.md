# ADR 0003 — First real connector: thevea calendar (user-credential), Clerk tenancy

- **Status:** Accepted (2026-07-07) — amended by [0004](0004-governed-calendar-import.md): the
  first use case is reframed to a source→destination **calendar import** (thevea becomes the
  read+write *destination*; a *source* connector is added). This ADR's connector components,
  Fernet storage, tenancy, and anti-fabrication rule all still hold; `thevea_booking` is demoted
  to a secondary example and `book_appointment` reshapes into `create_appointment`.
- **Deciders:** project owner + architecture session
- **Supersedes/relates:** amends ADR-0002 #11/#12 (first connector was Google Calendar OAuth —
  replaced by thevea) and #4's sequencing (first proven use case = headless calendar booking,
  not the voice agent; the voice design stands, only its queue position changes). Amends
  ADR-0001's implicit Supabase-Auth direction: auth is **Clerk**. Realizes PLATFORM_PLAN §9's
  top fix ("one real StateProvider") and effectively resolves the §10 sequencing question for
  the near term: the first workload is a headless connector-grounded booking agent.

## Context

Everything the run path verifies today is fixture-fed: the caller supplies the "state" the
checks evaluate against. Tool-level circularity was fixed (2026-07-03), but there is still no
state source the caller cannot fabricate, and tenancy columns exist but are never enforced.

The first real use case: **thevea.de** — German practice-management software for therapists
(physio, occupational, speech, podology). thevea has **its own calendar**; that calendar is the
system of record. thevea is an **external** company: no partnership, no public API. Access is
possible the same way the therapist accesses it — **with the user's own credentials** against
the endpoints thevea's own web app uses. An MCP server was considered as the access mechanism;
it is packaging, not a different mechanism — any MCP server would need the same
credential-based client underneath (see D6).

**Tenant isolation is central to the whole concept**: each practice (tenant) owns its calendar
and credentials; an agent must be *structurally unable* to touch another tenant's state.

## Success criterion

1. A therapist signs in via Clerk; their practice is a tenant; every API read/write is
   tenant-filtered.
2. They connect their thevea account (own credentials); the credential is stored encrypted on
   a tenant-scoped `Connection`; it never appears in logs, traces, or API responses.
3. `POST /api/v1/runs` for a `thevea_booking` template runs the loop against the **live**
   thevea calendar: **BLOCK** when the requested window is busy (precondition via the
   availability read); **OK** only when an independent post-execution re-query shows the
   appointment landed; **REJECT** when the write claimed success but nothing persisted;
   **STATE_UNAVAILABLE** when thevea is unreachable, the session is invalid, or the endpoints
   changed shape.
4. The `calendar` binding is never satisfiable from the request payload once a connection is
   bound.
5. A run in tenant A cannot read or write tenant B's calendar — not as a filter the agent
   obeys, but because the provider handed to the engine is constructed from tenant A's
   connection only.

## Decision

### D1 — System of record: thevea's own calendar, accessed as an external service
A `TheveaClient` speaks the HTTP endpoints thevea's web frontend uses: login → session
(cookie/JWT) → availability read → appointment write → appointment read-back. This is
reverse-engineered, not contracted. Named risks, accepted for the first test:
- **Fragility:** thevea can change endpoints at any time. Mitigation is structural: any
  unexpected response shape raises `StateUnavailable` → the run halts safely; it never acts on
  garbage. (This is the governance pitch demonstrating itself on a flaky SoR.)
- **ToS/legal:** automated access with the user's own credentials, on the user's behalf, to
  the user's own data is the defensible framing — but check thevea's ToS before any external
  demo, and never exceed the logged-in user's own permissions.
- **2FA/CAPTCHA:** if thevea enforces either on login, headless auth breaks. This is the
  first thing the M0 spike must answer.

### D2 — Auth & tenancy: Clerk, organizations = tenants, enforced everywhere
- Clerk on the Next.js frontend; FastAPI verifies the Clerk JWT via JWKS.
- **Clerk organization → `tenant` row; Clerk user → `app_user` row.** thevea's persona is the
  solo/small practice, so org-per-practice fits.
- The resolved `tenant_id` becomes a mandatory filter on every query (templates catalog stays
  global; instances, connections, runs, approvals are tenant-scoped). This closes the known
  "columns exist, never filtered" gap.
- Isolation layers: API filtering (this ADR) → runtime construction (D4, the structural one)
  → Postgres RLS (later hardening, not v1).
- Rejected: Supabase Auth (weaker org/multi-tenant ergonomics than Clerk, and the frontend is
  Next.js where Clerk's DX is strongest); building auth in-house (never).

### D3 — Credential storage: encrypted at rest in the existing `Connection` row
Fernet (`cryptography`) with a key from the environment; ciphertext in
`Connection.tokens_enc` (`tenant_id`, `type='calendar'`, `adapter='thevea'`). Stored payload:
the thevea credential plus the current session token + expiry; the client re-authenticates on
401/expiry and re-persists the session. Holding a raw password is heavier custody than an
OAuth token — the encryption key management and "never in logs" rule are non-negotiable, and
migrating to token/OAuth is the first ask if thevea ever becomes a partner.

### D4 — State routing: `RoutingStateProvider`, engine untouched
The engine already delivers each binding's declared `provider` to `StateProvider.query`. A
platform-side router dispatches `provider: thevea` → `TheveaStateProvider` (bound to the
run's resolved tenant Connection); anything else → the memory fixture as today.
**Anti-fabrication rule:** a binding routed to a real provider never falls back to the
fixture. **Isolation rule:** the provider is constructed per-run from the authenticated
tenant's connection — cross-tenant access has no code path.

### D5 — Checks stay pure functions of real state
- Precondition: `calendar.has_free_slot == true` — availability read over the requested window.
- Postcondition: re-resolved by the engine after execution against the read path
  (slot now occupied / appointment present for the window). No event-ID plumbing from the
  tool into the check — that would smuggle a claim into the evaluator.
- `verify: {retries: 2, backoff_s: 1}` absorbs read-after-write lag.
- Non-circularity is real: the write endpoint and the read endpoints are distinct surfaces of
  an external system neither the agent nor the platform controls.

### D6 — Tool + MCP positioning: connector first, MCP as later packaging
`book_appointment` is a tool in the existing `ToolRegistryAdapter`, implemented over
`TheveaClient`'s write endpoint. The considered MCP server is not an alternative: it would
wrap this same client. Sequencing: **M1** platform-internal connector (smallest path to a
governed real run); **M2** expose the same tools via a minimal stdio MCP server so a real LLM
agent can drive the identical template through the engine's existing `RunbookMcpServer`
(gate_step → scoped allowlisted proxy → verify_step). M2 is wiring, not architecture.

### D7 — GDPR: transient reads, booleans in the log, plus credential custody
Therapy calendars are health-context personal data. Calendar *content* is read transiently
and never persisted; the episode log records check results, state hashes, and slot windows —
never names, diagnoses, or notes (carries ADR-0002 #11's rule verbatim). The v1 test case
carries no patient identity in `case`. Credentials per D3.

### Run binding
`RunRequest` gains optional `connections: {role: connection_id}` (validated against the
caller's tenant; matches the `InstanceConnection` shape so the Stage-4 instance path drops in
later). The `Runtime` facade resolves connections → builds the routing provider +
connection-bound tool registry → `build_local_engine`. Engine, spec, compiler: unchanged.

## Milestones

- **M0 — spike (gates everything):** with a real thevea account, prove headless login and one
  calendar read. Answers: 2FA? CAPTCHA? endpoint shapes? session lifetime? If M0 fails, the
  fallback fork is (a) approach thevea for API access or (b) pick a first SoR with an API —
  decide then, not now.
- **M1 — headless governed run:** Clerk + tenant filtering; Connection create/connect flow;
  `TheveaStateProvider` + `book_appointment`; one seeded `thevea_booking` template; all four
  outcomes demonstrated against the live calendar.
- **M2 — the actual agent:** LLM agent drives the same template via `RunbookMcpServer`.

## Consequences

- New platform modules: `app/connections/` (Clerk-guarded connect flow, crypto),
  `app/providers/thevea.py` + `app/providers/routing.py`, `app/workloads/thevea_tools.py`,
  one `thevea_booking` template. New deps: `cryptography`, Clerk JWT verification (JWKS via
  `httpx` + a JWT lib — no Clerk SDK needed backend-side).
- No schema migration: `Connection` already carries every needed column.
- No engine change, no spec change, no compiler change.
- Tenancy enforcement lands as a prerequisite (first milestone), not an afterthought.
- ADR-0002 gains an "amended by 0003" status note; the Studio Stages 3–4, when resumed,
  target the thevea persona (configuration cockpit before authoring UI).

## Deliberately not building

Google Calendar connector · Studio UI (until after M1) · Twilio/voice surface · DB-backed
approval queue (no approval-gated step in the v1 template) · Postgres RLS (later hardening) ·
per-user sub-tenant connections · Temporal · a thevea MCP server before M2.

## Open questions

- M0 unknowns: thevea login mechanics (2FA/CAPTCHA), endpoint stability, session lifetime.
- thevea ToS reading before anything external-facing.
- Clerk org onboarding flow (who creates the org — self-serve vs invited) — product, not
  architecture; decide at Stage-4 design.