# ADR 0004 — Governed calendar import (source → destination sync), two-connector abstraction

- **Status:** Accepted (2026-07-08)
- **Deciders:** project owner + architecture session
- **Amends:** [0003](0003-thevea-calendar-connector.md) — the first use case is reframed from
  *booking a single appointment into thevea* to *importing an existing practice's appointments
  from its current booking system into thevea*. thevea stays the connector target but becomes the
  **destination** (read + write); a **source** connector is added. ADR-0003's decisions on
  credential connectors, Fernet storage, tenancy, and the anti-fabrication rule all still hold.

## Context

The reframed first use case (owner, 2026-07-08): a podology practice
(healthyfeet-podologie.de) already runs its calendar in some existing booking/admin system. The
agent should authenticate to that system with the practice's credentials, **read** its
appointments, and **write** them into the practice's **thevea** calendar — a one-directional
**onboarding migration / sync**, not an interactive booking. This is a sharper first workload:
it is a real thevea onboarding pain, headless (no LLM needed for v1), and the governance value
is concrete — *every appointment provably copied, none dropped, none duplicated, verified against
thevea's own read.*

This changes the single-system shape of ADR-0003 (one `calendar` connection that both reads
availability and writes a booking) into a **two-system** shape (a source we read, a destination
we read + write).

## Success criterion

Given a practice with a source booking system and a thevea account, an import run copies the
source's appointments into thevea such that:
1. Every created appointment is **verified against thevea's own read** (postcondition), never the
   write's claim.
2. The import is **idempotent** — re-running creates no duplicates (an already-present
   appointment is a governed skip, not a second write).
3. Nothing is **silently dropped** — the run reports source-count vs. created + skipped + failed,
   and flags any gap.
4. No appointment is **fabricated** — both the source read and the destination verify are
   grounded in the real systems (anti-fabrication, ADR-0003 D4).
5. It is **tenant-scoped** — a run only ever touches the calling tenant's own two connections.
6. **Append-only — never replace (D7).** The agent can only *create* appointments in thevea; it
   can never modify or delete an existing one. This is structural: the destination connector
   exposes no update/delete capability at all, and the idempotency precondition refuses to write
   when a matching appointment already exists.

**Source system (owner, 2026-07-08):** the source is a **REST admin API** at
`https://www.healthyfeet-podologie.de/api/admin/`, accessed with **preset credentials**
(configured ahead of time via server config/env for the demo, not entered per run).

## Decision

### D1 — One connector abstraction, two instances (`CalendarConnector`)
Formalize a `CalendarConnector` capability the platform programs against, so both systems are
"implement the connector, the runtime is unchanged" (PLATFORM_PLAN: runbook is data, agent is a
plugin):
- **read:** `list_appointments(window)` (source enumeration), `find_appointment(match)`
  (destination idempotency check + verify).
- **write:** `create_appointment(appt)` (destination only).
The **source** connector (healthyfeet) implements the read side; the **destination** connector
(thevea) implements read + write. Both are credential-based HTTP clients with their own captured
API (thevea = GraphQL/cookie per ADR-0003; the source = its own capture, see Open questions).
ADR-0003's `TheveaClient` generalizes into the destination connector and gains
`find_appointment` + `create_appointment` (its `has_free_slot` availability read is no longer
central).

### D2 — Two connection roles; the template references roles, the instance binds connectors
`required_connections: [source, destination]`. The template's state bindings reference **roles**
(`provider: source`, `provider: destination`), not concrete adapters, so the template is a reusable
*any-source → thevea* primitive. The instance binds each role to a concrete tenant `Connection`
(healthyfeet, thevea). `RoutingStateProvider` (built in ADR-0003 wiring) routes by role at run
time. No schema change — `InstanceConnection` already keys by role; `Connection` already carries
adapter/type/tokens_enc.

### D3 — The governed unit is ONE appointment (idempotent, verified copy)
The engine runs a fixed step sequence, so "sync all" is not a runbook concept — the per-appointment
copy is the contract:

The import is a **headless `workflow`-class** template (ADR-0002 #10) — no conversation, no
real-time surface. The executor is the registered `create_appointment` tool; there is no dialogue
agent. (owner, 2026-07-08: "no conversation is required".)

```yaml
template: calendar_import
agent_class: workflow            # headless — no dialogue
required_connections: [source, destination]
state:
  source_appt: { provider: source,      query: "appointment/{appt_id}" }
  dest_match:  { provider: destination, query: "appointment?ref={appt_id}" }
steps:
  - id: copy_appointment
    kind: enforced
    tools: [create_appointment]
    preconditions:
      - check: source_appt.exists == true      # real in the source (re-grounded per run)
      - check: dest_match.exists == false       # idempotency — not already copied
    execute: { adapter: registry, tool: create_appointment }
    postconditions:
      - check: dest_match.exists == true        # thevea's own read confirms it landed
    on_fail: halt
```

**Idempotency is structural, not detection:** the `dest_match.exists == false` precondition means
the engine *refuses to double-write* an already-imported appointment (it BLOCKs). A BLOCK here is
the desired "already synced" outcome, and the orchestration layer (D4) classifies a BLOCK whose
reason is "already in destination" as a **skip**, distinct from a real failure. The
`create_appointment` tool reads the full source appointment (by id) via the source connector and
writes it to the destination — both connectors are bound at resolve time.

### D4 — Enumeration + orchestration sit ABOVE the engine (deterministic loop in v1)
The engine governs one appointment; a **sync orchestrator** drives the set:
1. Authenticate to source, `list_appointments(window)` — the work-list (an orchestration read).
2. For each source appointment, start a governed run (case = its id).
3. Aggregate outcomes into a **completeness report**: created (OK), skipped-already-present
   (BLOCK: already in dest), failed (REJECT / STATE_UNAVAILABLE). Report source-count vs. the sum;
   flag any gap (no silent truncation — PLATFORM_PLAN §8 risk).
The per-run precondition re-reads the source, so a stale work-list can never cause a fabricated
copy. The orchestrator is a deterministic loop in v1 (honest, no LLM); an LLM agent could drive it
later (ADR-0003 M2) without changing the contract.

### D7 — Append-only, never replace (structural)
Enforced at two layers, so it cannot be bypassed:
- **Capability:** the `DestinationCalendar` connector exposes **only** `find_appointment` (read)
  and `create_appointment` (write). There is no `update`/`delete` method anywhere — the tool
  registry a destination run can call physically has no way to mutate or remove an appointment.
- **Contract:** the `dest_match.exists == false` precondition means an appointment that already
  exists in thevea is never written again (it BLOCKs → classified as a skip). So even re-runs
  only ever *append what is missing*.
This makes the safety property the owner asked for ("append and NEVER replaces") a structural
guarantee, not a convention — exactly the platform's "prevention, not detection" thesis.

### D5 — What survives vs. restructures (CLAUDE.md §IV — surgical)
**Survives unchanged:** `RoutingStateProvider`; credential `crypto`; the `Connection` model +
connection API; tenancy; the runner injection seam (`execute_contract(real_providers, extra_tools)`,
`resolve_connectors`) — it generalizes to resolve *two* connections into two providers + the
create tool.
**Restructures:** `thevea_booking` (single-slot booking) is demoted to a secondary example; the
new primary template is `calendar_import`. `TheveaClient` gains `find_appointment` +
`create_appointment`. **New:** a source `HealthyfeetConnector` (read-only, needs its own capture);
the `CalendarConnector` protocol; the sync orchestrator + a completeness-report DTO; an
`external_ref` concept to tag imported appointments for idempotent matching (D6).

### D6 — Idempotency identity
`dest_match` must recognize an already-imported appointment. Preferred: on create, tag the thevea
appointment with the **source appointment id** as an external reference, and `find_appointment`
queries by that ref. If thevea has no external-ref field, fall back to matching on
(start-datetime + patient) — weaker, flagged. Which is possible depends on thevea's schema (the
capture). Claim precisely: *"idempotent by source-id reference"* only if the ref field exists.

## Consequences

- **New modules:** `app/providers/healthyfeet.py` (source connector + StateProvider),
  `app/connectors/base.py` (the `CalendarConnector` protocol), `app/sync/orchestrator.py`
  (enumerate → per-appointment runs → report), `runbooks/calendar_import.yaml`. `TheveaClient`
  extends (find/create). `resolve_connectors` resolves both roles.
- **Frontend:** the Configure cockpit now shows **two** connect forms (source + destination) and,
  after deploy, an **Import** action that triggers the orchestrator and renders the completeness
  report (created / skipped / failed per appointment).
- **No engine change, no schema migration** — the two-connector shape is data over the existing
  `Connection`/`InstanceConnection`/routing seams. This is the test that ADR-0003's abstractions
  were right.
- **Governance invariants preserved:** prevention (refuses duplicate + unverified writes before
  acting), structural idempotency, checks are pure functions of source+dest state, runbook-is-data.
- ADR-0003 status gains an "amended by 0004" note; its M1 connector components are reused, its
  `thevea_booking` template demoted, its `book_appointment` tool renamed/reshaped to
  `create_appointment`.

## Deliberately not building

An LLM agent driving the sync (deterministic loop is enough for v1) · bidirectional sync /
conflict resolution (one-directional import only) · real-time incremental sync (a one-shot
onboarding import; scheduled re-runs are just re-runs, safe because idempotent) · a generic
connector marketplace (two concrete connectors) · the single-slot `thevea_booking` as the primary
path.

## Open questions

- **Source API shape** — mostly RESOLVED by recon (2026-07-09): the admin API at
  `https://www.healthyfeet-podologie.de/api/admin/` is **HTTP Basic auth** (confirmed via
  `WWW-Authenticate: Basic realm="Healthy Feet Admin"`), on Vercel/Next.js, with two live routes
  **`/api/admin/calendar`** and **`/api/admin/bookings`** (bogus creds rejected, so the preset
  creds are validated). `HealthyfeetConnector` now uses Basic auth + `/calendar`. **Only remaining
  fill-in:** the appointment **JSON shape** — run `backend/spikes/healthyfeet_source_spike.py`
  with the real credentials to reveal which route holds appointments and its field names, then map
  it into `_parse_appointment`.
- Whether thevea exposes an **external-reference field** for idempotent matching (D6).
- **Read-after-write consistency** on thevea — the verify postcondition may need
  `verify: {retries, backoff}` (already supported by the engine) for eventual consistency.
- Enumeration window/scope — RESOLVED (2026-07-09): a `window_from`/`window_to` date range
  parameter, **plus an unconditional safety filter** in the orchestrator that admits only
  **confirmed, future** bookings. Cancelled/rescheduled/still-`new` and past appointments are
  excluded and reported (`ImportReport.excluded` = `{ref, reason}`), never silently dropped and
  never written. This is a deliberate correctness guard for a real migration: it is not a toggle,
  because importing a cancelled or past appointment into a live practice calendar is always wrong.
  Implemented in `app/sync/orchestrator.py::_exclude_reason`; on the real source (143 bookings)
  it admits 14 and excludes 129 (126 past, 3 unconfirmed).