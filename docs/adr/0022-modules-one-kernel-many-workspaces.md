# ADR-0022 — One kernel, many modules: a customer's workspace is composed, never forked

Status: proposed · 18 October 2026 · builds on PLATFORM_PLAN §1 ("the control plane knows nothing
about the domain"), ADR-0006 (taxonomy), ADR-0012 (integration levels), ADR-0014 (systems
registry), ADR-0016 (workspaces, types as data), ADR-0017 (workspace knowledge) · amends ADR-0002
(templates are a global catalog → templates belong to a module), ADR-0014 (one registry → one
registration per module), ADR-0016 D2 (practice types → presets of the `praxis` module)

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
  - `app/connections/resolve.py:38-42,197-238` — adapter base URLs, role names
    (`source`/`destination`/`occupancy`/`site`) and the import/mirror tool imports are hard-coded;
    `app/config.py` carries thevea/healthyfeet/doctolib URLs as global settings.
  - `app/sync/jobs.py:35-42` — template name → orchestrator map in the platform worker.
  - `app/db/models.py` — `ImportJob` (`patients`, `forced`, `review`) and
    `CallerMemory.patient_id` sit beside the generic `Run`/`Task`.
  - `app/agents/config.py:44` — `AgentConfig` (`extra="forbid"`) *is* a practice: `practice_name`,
    opening hours, `resources` MA1–MA3, `treatments`, `booking_questions`, cancellation window.
  - `app/workloads/conversational/{booking,practice,calendar,capabilities}.py` — the conversational
    "surface" is a medical receptionist; `booking.TOOLS` is the only tool table.
  - Frontend: rail `components/studio/WorkspaceShell.tsx:28-37`, sections
    `features/agents/AgentWorkspace.tsx:34-53`, hand-built `ConfigEditor.tsx`, practice types in
    `features/agents/types.ts`.
- **Already shaped right, to be generalised rather than replaced:** the `VOICE_CALENDARS` registry
  with declared capabilities and a take-away-only `effective_config` (ADR-0014); practice types as
  data files (ADR-0016); skills as data with `requires` + `tools` (`skills/*/skill.json`);
  `ParameterSchemaForm` rendering a template's `parameters`; `GET /connections/systems`;
  `KnowledgeDocument` + pinning (ADR-0017); `Task`/`TaskEvent` + `tasks/state.py`.

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
procedure were provisioned by hand, and the procedure id is hard-coded. That is exactly where
laufwise is different, and D4 makes the difference structural.

## Success criterion

1. A real-estate workspace ships **without editing a kernel file** — checked by a test, not review.
2. **Delete `modules/praxis` and the kernel still imports and its tests pass** (CLAUDE.md §XII).
3. Two tenants run **different versions of the same module** side by side; upgrading one is a
   version bump gated by that module's evals.
4. **No module code path changes an external system except as a tool inside a governed run.**

## Decision

### D1 — Three layers, and what belongs to each

| Layer | What it is | Varies per customer? |
|---|---|---|
| **Kernel** | tenancy + auth (`app/auth`, `app/identity`), the governed loop and its wiring (`app/control_plane`), `Template`/`AgentInstance`/`Run`/`Approval`/`Task`/`TaskEvent`, the contract + compiler + publish gate (`app/templates`), connections + credential encryption (the generic part of `app/connections`), the three tier runtimes (task state machine; the *generic* Pipecat/Twilio surface; the workflow runner), phone numbers, knowledge documents, conversation recording, schedules, evals harness, trace | **never** |
| **Module** | one vertical's nouns and behaviour, as a versioned package: systems, entities, tools, skills, runbooks, presets, config schema, UI sections | per vertical (`praxis`, `immobilien`, …) |
| **Workspace** | a tenant's composition: which modules at which version, connections bound to the modules' roles, config values, presets applied, knowledge, overrides | **yes — this is the tailoring** |

The kernel's test of membership is PLATFORM_PLAN's test of a primitive: it is in the kernel only
if two unrelated verticals need it unchanged. "Patient", "room", "listing", "portal" fail that
test; "a durable task with an approval pause" and "a credential bound to a role" pass it.

### D2 — A module is a package with a manifest, registered — never imported by the kernel

```
backend/modules/<key>/
  module.yaml        key, version (semver), kernel_api, roles, systems, presets, sections
  systems/           one file per external system (connector + provider), quirks inside (§XII)
  tools.py           ToolSpecs the module contributes
  skills/<name>/     skill.json + skill.md (unchanged format)
  runbooks/*.yaml    the module's templates (roles, not adapters)
  presets/*.json     today's practice_types, generalised
  config.schema.json the module's part of an agent's config (JSON Schema)
  migrations/        tables the module owns, prefixed <key>_
  evals/             scenarios that gate a version bump
frontend/src/modules/<key>/   only the sections no generic renderer can draw (D5)
```

The kernel exposes **registries**; a module's `register(kernel)` fills them. The registries replace
every hard-coded map found in the audit: systems (`build_connector`, `_DEFAULT_BASE_URL`/
`_base_url_for`, `VOICE_CALENDARS`, the URLs in `config.py` — a system's base URL becomes a
setting the module declares), roles (`resolve.py`), orchestrators (`sync/jobs.py`), tools
(`booking.TOOLS` — this *is* ADR-0012 D1's tool-source registry, scoped to modules first), skills,
templates (the seed reads every installed module's `runbooks/`), presets (ADR-0016's loader, per
module). Capabilities stay what ADR-0014 made them — declared by a system, taken away and never
granted by `effective_config` — but the capability *vocabulary* belongs to the module.

Discovery is an explicit list in kernel settings (`MODULES = ["praxis"]`), not entry-point
magic: one line to read, one line to change (§III).

### D3 — A workspace tailors along a ladder; take the lowest rung that works

1. **Config values** — the module's `config.schema.json`, rendered by the generic form.
2. **Data** — presets applied, knowledge documents, prompt text, booking-question-style lists.
3. **Template variants** — a tenant-owned copy of a module template, under the same publish gate
   and immutable versioning. This amends ADR-0002: `Template` gains a nullable `tenant_id`
   (null = the module's catalog entry).
4. **Extension points** — a module template names slots (`before_publish`, `after_intake`) where a
   workspace inserts its own enforced steps. Inserted steps pass the same gate; the module's own
   steps cannot be removed by a variant.
5. **A customer module** — code for one customer only, in `backend/modules/customers/<slug>/`,
   reviewed by PR, versioned and evaluated like any module. Same contract, private catalog.
6. **Never** — editing the kernel or another module for one customer.

Rungs 1–4 are data and survive every module upgrade because they target declared names (schema
fields, roles, slots). Rung 5 is code, pinned to a `kernel_api` range. A workspace records
`(module, version)` in a new `workspace_module` row with its validated config — `Tenant` itself
gains nothing.

### D4 — The write seal extends to modules: read freely, act only through a governed run

A module's code and its UI may **read** anything its systems expose. A change to an external system
is **only** a tool called inside a governed run — precondition → allowlist → approval → execute →
postcondition (re-querying real state) → trace. The kernel API a module's UI gets is: read
endpoints, *start a run*, *decide an approval*. There is no "write a row to the customer's system"
endpoint, by the same absence-of-the-method rule as ADR-0004 D7. Module-owned tables
(`<key>_*`) hold the module's own records (a draft listing, an onboarding checklist); they are
not a side door to an external system.

This is the line Wonderful does not draw, and it is the product: *tailored to each customer, and
still every consequential action is governed and auditable.*

**Prerequisite:** the approval gate must be real. Today the factory wires `AutoApprovalGate`,
`/api/v1/approvals` returns `[]`, and `required_when` falls through to auto-approve (PLATFORM_PLAN
§9 re-check). D4 is theatre until that is fixed (CLAUDE.md §XIII: a gate that cannot block).

### D5 — UI: generic renderers first, compiled-in components second, no runtime bundles

A module declares its sections in `module.yaml`; the Studio rail is kernel sections + the
installed modules' sections. Section kinds, in order of preference:

- `config` — the module schema, rendered by `ParameterSchemaForm` (generalised to JSON Schema);
- `records` — a list/detail view over a module table or a read provider;
- `runs` — run history filtered to the module's templates;
- `approvals` — the inbox of pending decisions for this module's runs;
- `custom` — a named React component in `frontend/src/modules/<key>/`, compiled into the app.

Runtime-loaded bundles (Wonderful's Shadow-DOM apps + SDK) are deliberately not built: they make
the SDK a public contract we have to version and sandbox, for a need no customer has shown yet.
Revisit when a customer needs a UI we cannot ship through this repo.

### D6 — Versioning and upgrades

A module has a semver `version` and a `kernel_api` range. Its templates keep today's rule (a
published `(name, version)` is immutable; the seed skips existing ones). A workspace pins a module
version; an upgrade is a version bump, gated on the module's `evals/` passing against that
workspace's config (evals-and-proof). The kernel's public surface for modules is one package,
`app/kernel/` (re-exports only), so `kernel_api` has something concrete to version.

### D7 — The boundary is a test

A pytest walks imports with `ast`: no kernel module may import `modules.*`, and a module may import
only `app.kernel`. No new dependency (§VIII: import-linter would do it, a 40-line test does too).
During migration the test carries an explicit list of known violations; the list may only shrink.

### D8 — Checked against three use cases that share almost no nouns

| | Praxis voice reception (live) | Immobilien (asked for) | Back-office approval (ADAC-like) |
|---|---|---|---|
| Tiers (ADR-0006) | conversational + workflow (import, mirror) | workflow (owner onboarding), task (draft a listing), conversational (leads, viewings) | workflow with a human gate; task for analysis |
| Module provides | thevea/doctolib/healthyfeet systems, booking tools + skills, practice presets, room mapping | CRM + portal systems, listing/owner tables, `publish_listing` tool, onboarding runbook, presets per agency type | campaign tables, validation tools, the decision runbook |
| Governed write | book / cancel / move an appointment | publish a listing to a portal — approved by a person, postcondition re-reads the portal | activate a campaign after approval |
| Kernel gaps it exposes | none new | **real approval gate**, **task tier** (ADR-0006 D3), **file storage** for owner documents | real approval gate, task tier |

The same kernel carries all three; the gaps are kernel work every vertical needs, not real-estate
work. Which CRM and portal the real-estate customer runs (onOffice, FlowFact, Propstack,
ImmoScout24 are candidates) is unverified and decides the first `systems/` files.

### Migration — extract the seam from the vertical we have, before writing the second

Each step ships alone with the suite green; none changes behaviour for a live practice.

1. **Boundary test + empty `modules/praxis`** (D7) with today's violations listed.
2. **Registries** (D2) replace `build_connector`, the `resolve.py` maps, `sync/jobs.py`,
   `VOICE_CALENDARS`, `booking.TOOLS`; the praxis module registers what is hard-coded today.
3. **Move the vertical** into `modules/praxis`: `Appointment`/`Patient`/`PracticeCalendar` ports,
   the three systems, booking/practice/calendar/capabilities, skills, presets, runbooks,
   orchestrators, `ImportJob` (becomes a praxis-owned table — `Task` is already the kernel's generic
   durable work). Room ids leave global defaults (`runbooks/calendar_import.yaml:34`,
   `sync/orchestrator.py:292`) for connection config.
4. **Split `AgentConfig`**: a kernel part (name, locale, voice, instructions, skills, model,
   recipients, transfer number) + `module_config` validated by the module's schema. The published
   snapshot format changes, so the snapshot gets a `schema_version` and old snapshots are read
   through a one-way adapter (published agents are immutable — ADR-0002).
5. **`workspace_module` + rail from installed modules**; frontend section registry replaces
   `WorkspaceShell` GROUPS and the section allowlist; practice forms move to
   `frontend/src/modules/praxis/`.
6. **Kernel prerequisites**: real approval gate, task tier, file storage.
7. **`modules/immobilien`**, against one named customer and their real systems.

Steps 1–5 are the "copy twice before you abstract" moment (CLAUDE.md §X): the second vertical is
what proves the abstraction, so they are done *with* step 7's requirements in hand, not
speculatively.

## Not building

- A marketplace or cross-tenant catalog — promotion and immutable versions cover reuse for now.
- Runtime-loaded UI bundles or a public UI SDK (D5).
- Tenant-run code (Wonderful's sandboxed functions). Customer code is a reviewed module (D3 rung 5).
- Declarative HTTP integrations — ADR-0012 L3 stays rejected; L2 (MCP) stays the no-code path.
- Module-to-module dependencies. A module depends on the kernel only, in v1.

## Consequences

- The thesis in PLATFORM_PLAN becomes checkable (criteria 1–2) instead of aspirational.
- Agency workspaces (ADR-0016) keep working: an agency's clients are workspaces composed from the
  same modules; a "practice type" is a `praxis` preset.
- Steps 2–4 touch the live voice path; each needs the existing voice evals and the
  `tests/test_voice_eval_runner.py` assertions green before merge.
- The audit surfaced a gap worth fixing before any of this: `capabilities.resolve` always withholds
  `cancel_appointment`/`reschedule_appointment` from Studio-configured agents
  (`workloads/conversational/capabilities.py:25,65`) while the Studio shows Thevea's `changes`
  capability as available (ADR-0021 Implementation status).

## Open questions

1. Module migrations: one Alembic chain with branch labels per module, or a chain per module?
2. Several modules in one workspace that all want the phone number: does the kernel route a call
   between modules' skills, or is the conversational agent always owned by exactly one module?
3. Does pricing follow modules (a module is a line item)?
4. Who is the named real-estate customer, which systems do they run, and does their "govern
   everything" include actions a portal's API does not let us verify afterwards? A write we cannot
   re-read cannot have a postcondition, and per D4 cannot be a governed tool.
5. Should the CLAUDE.md §0/§XII enforcement table be updated to name `app/kernel` and `modules/`
   once step 3 lands? (It describes code that must exist — not before.)
