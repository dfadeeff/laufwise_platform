# ADR-0028 — A workspace chooses its modules; the first real-estate workflow is a deterministic document workflow

Status: accepted · 19 October 2026 · **D3/D4 amended by ADR-0031:** agents are chosen by kind; creating one adds its module · D1–D4 built (see Implementation status) · builds on ADR-0022
(one kernel, many modules), ADR-0006 (tiers), ADR-0017 (workspace knowledge) · **amends ADR-0022
D2:** modules live in `backend/app/modules/<key>/` and declare themselves with a typed `Module`
object, not a `module.yaml` · reorders ADR-0022's migration for the first real-estate customer.

## Context

A second customer runs a property-management business and wants its back office automated. The
first workflow it named is the annual **Betriebskostenabrechnung** for a rented
Eigentumswohnung: read the owners' association's Hausgeldabrechnung, the Grundsteuer notice, the
heating bill and the lease; decide what the tenant pays; split it over tenancies, vacancy and
heating rules; propose the next monthly advance; write the letters. Two test cases came with it
(`docs/use_cases/real_estate/bo/NK-Test-0{1,2}`), both full of legal traps (cable TV no longer
allocable since July 2024, Grundsteuer missing from the association's bill, a rented smoke
detector, a change-of-tenant reading fee, a month without a meter reading).

A real-estate organization was created in the Studio, and it showed what the platform is today:
"Built for your practice", "Meet your new receptionist", calendar imports. One login can belong to
several organizations and each is its own tenant (ADR-0003 D2, ADR-0016 D1), so the data was
isolated. But **nothing in a workspace said what it was for**: every workspace was a medical
practice, because the code had no other kind.

### What Wonderful does, read for inspiration (`Work_projects/wonderful/repos`, 2026-10-19)

| Wonderful | Take | Do not take |
|---|---|---|
| One blob layer behind a provider interface; a file row is a reservation until a HEAD check completes it; virus scan in a separate adapter that calls back with a signed verdict | the two-phase upload and the provider seam (step 2) | — |
| A knowledge pipeline of registered steps (source → ingest → structure → store), each run writing versioned data that is promoted by a pointer flip once evals pass; retrieval exposed as a tool | registered steps, versioned data, retrieval as a tool | agents reference a knowledge base **by slug**, so a re-index changes a released agent; we pin the data version (ADR-0022 D6) |
| Structured extraction against a JSON-Schema subset, validated afterwards | a typed extraction target | review of an extraction is approve/reject only; we let a person correct individual input fields |
| Procedures that suspend with a reason and resume with a token; approvals expire; runs claimed by compare-and-swap | all of it, for the approval gate (step 5) | whoever may start a run may approve it; we require an approver role and approver ≠ initiator |
| A catalog of typed extension entities, installed in dependency order with rollback, pinned by content digest | a typed manifest per module | new procedure node kinds need a core edit (a closed switch); ours are registries |
| Apps and functions write tables directly; the ADAC app posts approval decisions from the browser | — | the write seal (ADR-0022 D4) is the difference we sell |
| Procedures branch on `contains "BLOCK_STATUS: COMPLETE"` in model text | — | branch on typed outputs only |

## Success criterion

1. A workspace says which modules it runs; a new one is asked before anything else; existing
   practices keep working unchanged.
2. The **server** refuses what a workspace's modules do not offer: a practice's workflow or a
   voice agent in a property-management workspace is a 403, not a hidden button.
3. Deleting a module package and its line in `INSTALLED` leaves the kernel importable, and a
   module reaches the kernel only through `app.kernel` — checked by a test.
4. The Betriebskosten computation is deterministic: the same documents give the same settlement,
   every figure traces to a document, and anything the documents leave open blocks the figures.

## Decision

### D1 — A module is a vertical; a workflow is a part of it. That is the cohesion line.

Modularity without cohesion is a pile of plugins that each redefine "unit" and "tenancy";
cohesion without modularity is the medical-practice code we have now. The line:

- **Workflows that share nouns share a module.** A Betriebskostenabrechnung, a Kautionsabrechnung
  and a Mieterhöhung all speak of a unit, a tenancy, an owners' association: they are parts of
  `immobilien` (`app/modules/immobilien/betriebskosten/`, later `…/kaution/`).
- **A vertical that shares no nouns is a separate module.** `praxis` and `immobilien` share
  nothing but the kernel.
- **Modules never import each other** (ADR-0022 "Not building"). Something two modules both need
  is a kernel candidate, and it enters the kernel only once two verticals need it unchanged.

### D2 — A module declares itself with a typed object, in `app/modules/<key>/`

`app/modules/<key>/__init__.py` exports `MODULE: Module` (`app/kernel/modules.py`): key, label,
summary, the rail pitch, the Agents intro, the runbook templates it owns, and the kernel surfaces it
turns on (`voice_agents` today). A typed object rather than ADR-0022's `module.yaml`, because the
manifest will reference code (tools, calculators, case types) and a typed reference fails at
import, not at first use. Under `app/` rather than `backend/modules/`, because the package build
includes `app*` only and nothing is gained by a second top-level package.

The kernel loads `INSTALLED = ("praxis", "immobilien")` by name and validates them together: a
module declaring the wrong key, asking for a surface the kernel lacks, or claiming a template
another module claims stops the process at boot.

### D3 — A workspace records its modules; a new one chooses

`workspace_module (tenant_id, module_key)`, unique per pair, deleted with its tenant. The migration
gives `praxis` to every workspace that holds anything (an agent, a deployment, a connection, a
document, a service) and to the legacy `default` tenant; an empty workspace gets nothing and the
Studio asks "What is this workspace for?". `GET /workspace/modules` returns what it runs and what it
could run; `POST /workspace/modules` adds one. There is no removal: a module's agents and runs would
need somewhere to go first. Choosing is open to any member, like every other workspace setting
today; restricting it to an organization admin is an open question.

This is ADR-0022 D3's `workspace_module` without `data_version` and `config`: they arrive with the
first thing that needs them (§III), the Betriebskosten settings in step 4.

### D4 — The kernel enforces ownership; the Studio only reflects it

- Deploying a template a module owns needs that module (`deploy_refusal`, 403). A template no
  module claims was authored in the Studio and is not restricted.
- Creating a voice agent needs a module offering `voice_agents` (403).
- Every runbook in `backend/runbooks/` belongs to exactly one module, and every template a module
  claims exists (`tests/test_module_boundary.py`).
- The Studio's rail pitch and Agents intro come from the module when the workspace runs exactly
  one; otherwise neutral kernel wording. Voice-agent setup appears only with `voice_agents`;
  workflows are filtered to the workspace's modules.

### D5 — The real-estate agent is a deterministic workflow with a model only at the edges

Tier: **workflow** (ADR-0006). The settlement is computed, never generated:

```
nebenkosten_abrechnung (runbook, step 4)
 1 extract   model: documents → Case        post: valid Case, every amount found verbatim in the source text
 2 classify  catalogue lookup               post: no unclassified position (else a question)
 3 review    approval: a person confirms the Case and answers the questions
 4 settle    settle(case, catalogue, policy) post: verify(case, settlement) == []
 5 draft     model: letters from the settlement   post: every number in the text is in the settlement
 6 send      approval, then export or send
```

- **Inputs are corrected, never outputs.** A wrong figure is fixed in the `Case` and step 4 runs
  again; there is no field in which a person, or a model, types a result.
- **Allocability is data.** `catalogue.yaml` maps a cost category to allocable / not / only if the
  lease names it, with its citation and an optional cut-off date. A new kind of cost is a row.
- **Splits are exact.** Whole cents, largest remainder: the parties' shares of a position always
  add up to it, so the Probe holds by construction.
- **Open questions block figures.** A settlement with a question carries no statements at all.
- **Workspace decisions are policy, not code**: e.g. whether the association's figure for the unit
  may stand in for the building total when the lease and the association split by different keys.

The core (steps 2, 4 and the postcondition) is built and tested against both cases.

### D6 — The kernel services this needs, built generically

Each is built once for every module; none names a real-estate noun.

| Service | Shape |
|---|---|
| **Files** | `stored_file` (tenant, sha256, sniffed media type, size, storage key, kind, retention), Supabase Storage behind a provider protocol, two-phase upload (reserve → upload direct → complete after a HEAD check). Today knowledge keeps only extracted text. |
| **Cases** | `case` (tenant, module, case type, status, data, the catalogue and template versions used, run ids). A module registers a case type: its input schema, its compute function, its postcondition. The review screen is rendered from the input schema, with each value linked to where it was read. |
| **Approvals** | ADR-0022 D4: persisted, tenant-scoped, an approver role, approver ≠ initiator, expiry, resume by re-execution. |
| **Knowledge** | stays ADR-0017 for now; the versioned pipeline is built when a module needs retrieval over many documents. |

Case files (the documents of one settlement) and knowledge (what every run may consult) are kept
apart: different lifetimes, different retention, different access.

## Build order

| Step | What | Test that says it is done |
|---|---|---|
| **1** | Workspace modules (D2–D4) | boundary test; 403s for a property workspace; existing suite unchanged |
| **2** | File store (D6) | upload, sniff, hash, tenant scoping, retention sweep |
| **3** | Cases and the review screen (D6) | a case type registered by a module renders, edits and re-computes |
| **4** | `immobilien/betriebskosten` plugs in: extraction, runbook, settings, letters | NK-01 and NK-02 end to end from the PDFs, scored against the extraction fixtures |
| **5** | The approval gate (ADR-0022 D4) | a run pauses at review and send, resumes only with an approver who is not the initiator |

Schedules (a reminder before the § 556 BGB deadline, an inbox trigger) come after step 5.

## Not building

- A scheduler for these workflows: they start when documents arrive.
- OCR for scanned documents, until a customer sends scans.
- Vector retrieval: a settlement reads a handful of known documents, not a corpus.
- Removing a module from a workspace, and a UI to add a second one.
- Module data versions and per-workspace module config (D3), until step 4 needs them.

## Consequences

- A property-management workspace is a first-class workspace, not a practice with hidden buttons.
- Every existing practice keeps working: it was backfilled to `praxis`, and the default tenant
  is created as one.
- The Run history's "Calls" tab and the Dashboard still speak about calls; they move behind module
  sections with ADR-0022 D5.
- Test fixtures that create a fresh workspace and deploy a practice workflow must give it `praxis`.

## Open questions

1. An expert answer key for NK-01 and NK-02, and a lawyer's review of `catalogue.yaml`
   (`reviewed: false`); the smoke-detector maintenance and the cost-basis rule are the two
   contested points.
2. Who may choose a workspace's modules: any member, or only a Clerk organization admin?
3. `/templates` has no authentication: anyone who can reach the API can save and publish a
   template into the global catalog that every workspace deploys from. Separate fix, before more
   customers.
4. What the customer's documents really look like (several Verwalter formats, scans?) decides how
   hard step 4's extraction is.

## Implementation status (2026-10-19)

- **D2:** `app/kernel/modules.py` (`Module`, `INSTALLED`, `SURFACES`, `installed`, `validated`,
  `owner_of`, `deploy_refusal`, `offers`); `app/kernel/__init__.py` re-exports;
  `app/modules/praxis/__init__.py`, `app/modules/immobilien/__init__.py`.
- **D3:** `WorkspaceModule` (`app/db/models.py`), migration `8b9c0d1e2f3a` with the backfill,
  `repo.workspace_module_keys` / `add_workspace_module`, `repo.default_tenant` creates a practice,
  `GET`/`POST /workspace/modules` (`app/api/v1/workspace.py`), `deps.workspace_modules`.
- **D4:** refusals in `app/api/v1/instances.py` and `app/api/v1/agents.py`;
  `tests/test_module_boundary.py`, `tests/test_workspace_modules.py`; Studio:
  `WorkspaceShell.tsx` (`useWorkspaceModules`, module pitch), `app/studio/page.tsx`
  (`ChooseModule`, gated voice setup, filtered workflows).
- **D5 core:** `app/modules/immobilien/betriebskosten/` (`model`, `calculate`, `verify`,
  `catalogue.yaml`) and `tests/test_betriebskosten.py` against `tests/fixtures/betriebskosten/`.
