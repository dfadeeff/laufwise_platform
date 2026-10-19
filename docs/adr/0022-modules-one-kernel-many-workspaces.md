# ADR-0022 — One kernel, many modules: a customer's workspace is composed, never forked

Status: proposed · 18 October 2026 · builds on PLATFORM_PLAN §1 ("the control plane knows nothing
about the domain"), ADR-0006 (taxonomy), ADR-0008 (named transitions), ADR-0012 (integration
levels), ADR-0014 (systems registry), ADR-0016 (workspaces, types as data), ADR-0017 (workspace
knowledge) · amends ADR-0002 (templates are a global catalog → templates belong to a module),
ADR-0014 (one registry → one registration per module), ADR-0016 D2 (practice types → presets of
the `praxis` module) · **revised 18 October 2026** after a code audit: module versions pin data,
not code (D6); the config split keeps the snapshot format (D3); the write seal is scoped to systems
of record and made structural (D4); conversational contracts cannot carry an approval step (D4);
mutation is decided per port (D9). The step-by-step build is in
[`docs/design/modular-platform-build-plan.md`](../design/modular-platform-build-plan.md).
**Amended by ADR-0028 (19 October 2026):** modules live in `backend/app/modules/<key>/` and
declare a typed `Module` object (D2); `workspace_module` is built without `data_version`/`config`
for now (D3); for the real-estate customer the order is workspace modules → file store → cases →
the Betriebskosten module → approval gate.

## Context

A real-estate customer asks for one application that governs *everything* they do: owner
onboarding, creating and publishing a listing ("Anzeige erstellen"), leads, viewings. That is a
second vertical, and it differs from the first in almost every noun: no patients, no rooms, no
treatments — listings, owners, documents, portals. The next customer will differ again.

Two answers are wrong. **One-size SaaS** (every tenant gets the same screens) is what customers no
longer accept. **A fork per customer** gives each one what they asked for and leaves us maintaining
N products that cannot take a fix or an upgrade. The answer this ADR records is the third one:
*every customer gets a tailored workspace, and every workspace is a composition of versioned parts
running on one kernel that never changes per customer.*

### What the code is today (audited 2026-10-18, see the "Implementation status" sections in 0001–0021)

- The **engine** (laufwise package) is domain-free, as PLATFORM_PLAN promises.
- **Everything above it is the medical-practice vertical**, mixed into what reads as platform code:
  - `app/connectors/base.py` — the generic connector seam holds `Appointment`, `Patient`
    (`vorname`/`geburtsdatum`), `PracticeCalendar`, `AppointmentLifecycle`; `build_connector`
    (`:380`) is one if/elif for every system.
  - Base URLs live twice — `_DEFAULT_BASE_URL` (`app/connections/resolve.py:39-43`) and
    `_ADAPTER_BASE_URL` read by `_base_url_for` (`app/api/v1/connections.py:49`) — over the
    thevea/healthyfeet/doctolib URLs in `app/config.py:32-42`. `resolve.py` also hard-codes the
    role names (`source`/`destination`/`occupancy`/`site`, `:197-238`) and imports the import and
    mirror tools at module top.
  - `app/sync/jobs.py:35-42` — template name → orchestrator map in the platform worker.
  - `app/db/models.py` — `ImportJob` (`patients`, `forced`, `review`) and
    `CallerMemory.patient_id` sit beside the generic `Run`/`Task`; `Approval` has no `tenant_id`.
  - `app/agents/config.py:44` — `AgentConfig` (`extra="forbid"`) *is* a practice: `practice_name`,
    opening hours, `resources` (default MA1–MA3), `treatments`, `booking_questions`, cancellation
    window. `make_snapshot` hard-codes the three voice contract names it pins
    (`app/agents/service.py:148`).
  - `app/workloads/conversational/` — the Pipecat/Twilio plumbing in `surface.py` is generic, but
    it builds a `BookingSession` inline (`:774`), gates transfer on opening hours (`:551`), and
    `ToolSpec.call` is typed `Callable[[BookingSession, dict], dict]` (`booking.py:1434`);
    `booking.py:1437` loads the YAML practice at import time.
  - Frontend: rail `components/studio/WorkspaceShell.tsx:28-37`, sections
    `features/agents/AgentWorkspace.tsx:34-53`, hand-built `ConfigEditor.tsx`, practice types in
    `features/agents/types.ts`. `ParameterSchemaForm` is a parameter-schema *editor*;
    `ParameterField` renders only `text|enum|bool|int`.
- **A published agent is less immutable than ADR-0002 says.** The snapshot pins `knowledge`,
  `contracts` and `base_prompt` (`service.py:164-171`), but skill prompts are read from disk on
  every call (`surface.py:231` → `skill_prompts`) and tool descriptions come live from
  `booking.TOOLS`. Editing a `skill.md` changes every published agent.
- **Already shaped right, to be generalised rather than replaced:** the `VOICE_CALENDARS` registry
  with declared capabilities and a take-away-only `effective_config` (ADR-0014); practice types as
  data files (ADR-0016); skills as data with `requires` + `tools`; `GET /connections/systems`;
  `KnowledgeDocument` + pinning (ADR-0017); `Task`/`TaskEvent` + `tasks/state.py`; and ADR-0008's
  rule that a mutation is a *named transition with its own contract*, never an `update` method.

### Reference: how Wonderful does it (read from `Work_projects/wonderful/repos`, 2026-10-18)

An *App* there is a workspace-scoped git repo (`tenants/{t}/workspaces/{w}/apps/{id}`):
`wonderful.json` manifest + a React bundle the host `import()`s into a Shadow DOM + `functions/`
(http/cron) + `tables/*.json` + optional assistant skills. A push builds in a sandbox, applies
tables → inactive version → functions → activate; previews per branch; `wful apps promote` moves
the repo between environments; `package:plugin` turns it into a catalog entry. The platform
supplies primitives (tables, functions, procedures with approval nodes, agents, secrets, roles, a
UI kit, `@wonderful/app-sdk`). Tailoring = composition in git per workspace.

Two lessons. **Take** the shape: the customer-specific thing is a versioned, promotable package
that composes platform primitives. **Do not take** the write path: the SDK's `api.post` and a
function's `tables.update` write directly, so an app can act without passing a gate. In the ADAC
demo the governed part is one procedure graph; the app around it is ungoverned, its tables and
procedure were provisioned by hand, and the procedure id is hard-coded. That is where laufwise is
different, and D4 makes the difference structural.

## Success criterion

1. A real-estate workspace ships **without editing a kernel file** — checked by a test, not review.
2. **Delete `modules/praxis` and the kernel still imports and its tests pass** (CLAUDE.md §XII).
3. Two tenants run **different data versions of the same module** (templates, skill prompts, tool
   descriptions, presets) side by side; moving one forward is a re-pin gated by that module's evals.
4. **No module code path changes a system of record bound to a role except as a tool inside a
   governed run.** Kernel channels (call-summary email, Twilio number wiring) are out of scope.

## Decision

### D1 — Three layers, and what belongs to each

| Layer | What it is | Varies per customer? |
|---|---|---|
| **Kernel** | tenancy + auth (`app/auth`, `app/identity`), the governed loop and its wiring (`app/control_plane`), `Template`/`AgentInstance`/`Run`/`Approval`/`Task`/`TaskEvent`, the contract + compiler + publish gate (`app/templates`), connections + credential encryption (the generic part of `app/connections`), the three tier runtimes (task state machine; the *generic* Pipecat/Twilio surface; the workflow runner), channels (phone numbers, notifications), knowledge documents, conversation recording, schedules, evals harness, trace | **never** |
| **Module** | one vertical's nouns and behaviour, as a package: systems, ports, entities, tools, skills, runbooks, presets, config model, conversation kit, UI sections | per vertical (`praxis`, `immobilien`, …) |
| **Workspace** | a tenant's composition: which modules at which data version, connections bound to the modules' roles, config values, presets applied, knowledge, template variants | **yes — this is the tailoring** |

The kernel's test of membership is PLATFORM_PLAN's test of a primitive: it is in the kernel only
if two unrelated verticals need it unchanged. "Patient", "room", "listing", "portal" fail that
test; "a durable task with an approval pause" and "a credential bound to a role" pass it.

### D2 — A module is a package, registered — never imported by the kernel

```
backend/modules/<key>/
  module.yaml        key, kernel_api, roles, sections, data_version
  __init__.py        MODULE: Module — register(kernel) fills the kernel registries
  systems/           one file per external system (connector + provider), quirks inside (§XII);
                     the only place in a module allowed to import an HTTP/browser client (D4)
  ports.py           the module's read and write protocols, split
  conversation/      its ConversationKit (D7), tools, skills/<name>/{skill.json,skill.md}
  runbooks/*.yaml    the module's templates (roles, not adapters)
  presets/*.json     today's practice_types, generalised
  config.py          its agent config model (D3); the JSON Schema is generated from it
  repo.py            the module's queries (amends CLAUDE.md §0 "repo.py is the only place")
  migrations/        an Alembic branch (`branch_labels=(key,)`) for tables it owns, prefixed <key>_
  evals/             scenarios that gate a data-version re-pin
frontend/src/modules/<key>/   only the sections no generic renderer can draw (D5)
```

The kernel exposes **registries** (`systems`, `processes`, `orchestrators`, `conversation`,
`agent_configs`, runbook/skill/preset directories, `sections`); a module's `register(kernel)` fills
them, and a duplicate key is a boot error. They replace every hard-coded map found in the audit:
`build_connector` and both base-URL maps (a system's base URL becomes a setting it declares),
`VOICE_CALENDARS`, the `resolve.py` role wiring, `sync/jobs.py`, `booking.TOOLS`, the seed's single
`runbooks/` directory, the contract names in `make_snapshot`. `booking.TOOLS` becoming a registry
*is* ADR-0012 D1's tool-source registry, scoped to modules first. Capabilities stay what ADR-0014
made them — declared by a system, taken away and never granted by `effective_config` — but the
capability *vocabulary* belongs to the module.

Discovery is an explicit list in kernel settings (`MODULES = ["praxis"]`), not entry-point
magic: one line to read, one line to change (§III).

### D3 — A workspace tailors along a ladder; take the lowest rung that works

1. **Config values** — the module's config model, rendered by the generic form.
2. **Data** — presets applied, knowledge documents, prompt text, booking-question-style lists.
3. **Template variants** — a tenant-owned copy of a module template, under the same publish gate
   and immutable versioning (amends ADR-0002). `Template` gains `module` and a nullable
   `tenant_id`; uniqueness becomes two partial indexes (catalog rows unique on `(name, version)`
   where `tenant_id IS NULL`, variants on `(tenant_id, name, version)`), because Postgres treats
   NULLs as distinct. Existing names stay as they are — they are referenced literally — and new
   modules prefix theirs (`immo_*`); the registry refuses a duplicate name at boot.
4. **Extension points** — named slots in a module template where a workspace inserts enforced
   steps. *Deferred:* the compiler could splice them, but no customer has needed one that a
   variant (rung 3) could not express (§III).
5. **A customer module** — code for one customer only, in `backend/modules/customers/<slug>/`,
   reviewed by PR and evaluated like any module. Same contract, private to that workspace.
6. **Never** — editing the kernel or another module for one customer.

A workspace records `(module_key, data_version, config)` in a new `workspace_module` row
(unique per tenant and module); `StudioAgent` gains `module_key` (default `praxis`); `Tenant`
gains nothing. Every existing tenant is backfilled with one `praxis` row.

**The agent config splits by inheritance, not by a new format.** `KernelAgentConfig` holds what
every agent has (name, locale, voice, greeting, instructions, skills, recipients, transfer number,
knowledge, retention, recall); `PraxisAgentConfig(KernelAgentConfig)` *is* today's `AgentConfig`,
field for field. The snapshot stays flat; an optional `"module": "praxis"` key is added, and a
missing key means praxis. The kernel reads its fields by projection. No published snapshot is
rewritten or parsed differently, and `instance_config` — which runs on every inbound call
(`runtime.py:33`) — keeps its behaviour. The JSON Schema for the Studio is generated from the
model (`model_json_schema()`), so there is one source of truth.

### D4 — The write seal extends to modules: read freely, act on a system of record only in a run

A module's code and UI may **read** anything its systems expose. A change to a **system of record
bound to a role** (a calendar, a CRM, a portal) is **only** a tool called inside a governed run —
precondition → allowlist → approval → execute → postcondition (re-querying real state) → trace.
Module UIs get read endpoints, *start a run*, *decide an approval*; there is no "write to the
customer's system" endpoint (ADR-0004 D7's absence-of-the-method rule). Module-owned tables
(`<key>_*`) hold the module's own records (a draft listing, a checklist); they are not a side door.

Today this is policy, not structure: the import's post-run review builds a full destination client
(`sync/orchestrator.py:152-163`) and `BookingSession` holds a write-capable `PracticeCalendar` for
the whole call. It becomes structural three ways: **(a)** every port is split into a read protocol
and a write protocol; **(b)** write-capable clients are built only inside a process's `wire()`
function, which only the runtime calls — orchestrators and read endpoints receive read views;
**(c)** the boundary test (D8) bans HTTP and browser client imports in a module outside its
`systems/`, and calls to write builders outside `wire()`.

**Prerequisite: a real approval gate, and it must respect the two clocks.** Today the factory wires
`AutoApprovalGate`, `/approvals` returns `[]`, `required_when` falls through to auto-approve, and
`approval` has no `tenant_id`. The gate becomes persisted (tenant-scoped rows, decided via the API)
and a run that needs a decision **resumes by re-execution**: the decided run is run again with the
same case, and its preconditions re-check real state, so nothing before the gate can double-write.
A voice contract runs inline in a live call and cannot wait for a reviewer, so **the publish gate
refuses an approval step in any contract a conversational agent pins**; a conversational agent
hands approval-needing work to a task (CLAUDE.md §XIII: a gate that cannot block is theatre).

This is the line Wonderful does not draw, and it is the product: *tailored to each customer, and
still every consequential action is governed and auditable.*

### D5 — UI: generic renderers first, compiled-in components second, no runtime bundles

A module declares its sections in `module.yaml`; the Studio rail is kernel sections + the installed
modules' sections, and an agent's sections come from its module. Section kinds, in order of
preference: `config` (a JSON-Schema form — a new renderer; today's `ParameterField` covers four
scalar types), `records` (list/detail over a module table or a read provider), `runs` (run history
filtered to the module's templates), `approvals` (pending decisions for its runs), and `custom` (a
named React component in `frontend/src/modules/<key>/`, compiled into the app). Praxis keeps its
hand-built `ConfigEditor` as a `custom` section — moving it is zero-risk; the generic renderer is
built for the second module.

Runtime-loaded bundles (Wonderful's Shadow-DOM apps + SDK) are deliberately not built: they make
the SDK a public contract we have to version and sandbox, for a need no customer has shown yet.

### D6 — A module version pins data; code stays compatible with every pinned version

Python code exists in one version per process, so two tenants cannot run two versions of a
module's *code*. What a workspace pins is the module's **data**: templates (already immutable per
`(name, version)`), and — newly — skill prompts and tool descriptions, pinned into the published
snapshot exactly as knowledge is (closing the gap in Context). A module's code must stay
compatible with every data version still pinned by a published agent; the module's evals run
against each pinned version in CI. Moving a workspace forward is a re-pin, gated on the module's
evals passing against that workspace's config (evals-and-proof). `kernel_api` versions the one
package modules may import, `app/kernel/` (re-exports only).

### D7 — One agent belongs to exactly one module; the conversation splits at a kit

The generic surface (pipeline, STT/TTS/realtime, VAD, idle ladder, timers, call ending, number
speech, transcript observer, tool threading, transfer and end-call mechanics, recording, draining)
stays kernel. Everything a receptionist knows moves behind a `ConversationKit` the module supplies:
a new session state, its tools, its instruction block, per-config tool properties, whether a
transfer is allowed now, and how a call summary is delivered. `ToolSpec` becomes generic over the
kit's state. A phone number points at one agent (`VoiceChannel.agent_id` is unique,
`models.py:334`) and an agent at one module, so the kernel never routes a call between modules.

### D8 — The boundary is a test

A pytest walks imports with `ast`: no kernel module may import `modules.*`; a module may import
only `app.kernel`; plus D4's import bans. No new dependency (§VIII). During migration the test
carries an explicit list of known violations; it asserts both that nothing new appears and that no
listed entry is stale, so the list can only shrink.

### D9 — Mutation is decided per port, as a named transition

ADR-0004 D7's "no update/delete" was a calendar-import decision. A listing must change price, be
deactivated, be withdrawn. ADR-0008 already shows the shape: a mutation exists only as a **named
method** on a port that declares it (`cancel_appointment`, not `update`), with its own runbook and
a postcondition that re-reads the result. Modules follow that rule. A write whose result the
system does not let us re-read cannot have a postcondition and is therefore **not offered** as a
tool; a write the system ingests later (a portal feed) ends its run `submitted` and a task
schedules the verifying read.

### D10 — Checked against three use cases that share almost no nouns

| | Praxis voice reception (live) | Immobilien (asked for) | Back-office approval (ADAC-like) |
|---|---|---|---|
| Tiers (ADR-0006) | conversational + workflow (import, mirror) | workflow (owner onboarding, publish), task (draft an exposé), optional conversational (leads, viewings) | workflow with a human gate; task for analysis |
| Module provides | thevea/doctolib/healthyfeet systems, booking kit + skills, practice presets, room mapping | CRM + portal systems, owner/property/draft tables, publish/deactivate tools, runbooks, agency presets | campaign tables, validation tools, the decision runbook |
| Governed write | book / cancel / move an appointment | publish a listing — approved by a person bound to a draft revision; postcondition re-reads the portal | activate a campaign after approval |
| Kernel gaps it exposes | snapshot pinning of skills/tools | **approval gate**, **task tier** (ADR-0006 D3), **file storage** for owner documents | approval gate, task tier |

The same kernel carries all three; the gaps are kernel work every vertical needs, not real-estate
work. Which CRM and portal the customer runs (onOffice, FlowFact, Propstack, ImmoScout24 are
candidates) is unverified and decides the first `systems/` files.

### Migration order

In short (each step and its test in the build plan): **0** pin skill prompts and tool
descriptions into the snapshot → **1** kernel facade + boundary test → **6a** real approval gate →
**2a/2b** system, process and orchestrator registries → **3a** config split by inheritance →
**4** `workspace_module` (backend) → **6c** file storage → **6b** task tier → **2c** conversation
kit → **3b** move the vertical into `modules/praxis` → **5** frontend section registry →
**7** `modules/immobilien`. The conversation-kit extraction touches the live voice path and goes
last among the refactors, behind the voice eval gate and a shadow call on a staging number.
Discovery for step 7 (the customer's systems) starts in parallel with step 1 — the second vertical
is what proves the abstraction (CLAUDE.md §X), so the refactors are done with its requirements in
hand.

## Not building

- A marketplace or cross-tenant catalog — promotion and immutable versions cover reuse for now.
- Runtime-loaded UI bundles or a public UI SDK (D5).
- Tenant-run code (Wonderful's sandboxed functions). Customer code is a reviewed module (D3 rung 5).
- Template extension slots, until a variant cannot express a need (D3 rung 4).
- Declarative HTTP integrations — ADR-0012 L3 stays rejected; L2 (MCP) stays the no-code path.
- Module-to-module dependencies, and cross-module call routing (D7). A module depends on the
  kernel only, in v1.
- Renaming live tables. `import_job`, `caller_memory`, `voice_channel` stay in the kernel
  migration chain as grandfathered praxis tables; the `<key>_` prefix applies to new tables.

## Consequences

- The thesis in PLATFORM_PLAN becomes checkable (criteria 1–2) instead of aspirational.
- Agency workspaces (ADR-0016) keep working: an agency's clients are workspaces composed from the
  same modules; a "practice type" is a `praxis` preset.
- Published agents become actually immutable (D6), which is worth doing even if nothing else here
  is built.
- Alembic moves from "exactly one head" (`tests/test_migrations.py:22`) to one head per branch
  label, and the deploy runs `upgrade heads`.
- The audit surfaced a gap to fix before any of this: `capabilities.resolve` withholds
  `cancel_appointment`/`reschedule_appointment` from every Studio-configured agent
  (`workloads/conversational/capabilities.py:25,65`); no live path reaches them, while the Studio
  shows Thevea's `changes` capability as available (ADR-0021 Implementation status).

## Open questions

1. Who is the named real-estate customer, which CRM and portal do they run, and can each write be
   re-read (D9)? This decides whether `publish_listing` can be a governed tool at all.
2. `required_when` with field expressions: can the compiler lower it onto the engine's
   `risk >= level` gate, or does it need an engine change (the engine is closed for modification)?
   To be checked against laufwise's `ApprovalGate` signature before step 6a.
3. Does pricing follow modules (a module is a line item)?
4. Should CLAUDE.md §0/§XII/§XIII be updated? Two statements are already wrong today (§XIII calls
   `app/workloads/conversational/` a placeholder; §0 has one base-URL map where there are two);
   the module layout should be added once step 3b lands, not before.
