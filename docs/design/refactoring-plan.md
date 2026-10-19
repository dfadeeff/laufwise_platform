# Studio refactoring proposal

Date: 6 September 2026. Proposal only; product code has not been changed.

> **Status, 18 October 2026.** Most of this plan is built. The Studio rebuild landed in #80
> (`a103851`, named agents, `StudioAgent`/`VoiceChannel`, typed room mapping, tenant-checked
> binding) and #85 (`b201a37`, the workspace sections), followed by single-use media tokens
> (`8ce1e6d`), the multi-tenant clean-up (`3e1e837`), call outcomes/costs (`5b090fc`), self-serve
> numbers (#96), real-calendar test calls (#101, ADR-0018) and Thevea cancel/move (#117, ADR-0021).
> Each section below ends with a **Status** line. Not built: connection configuration
> generation and an update endpoint, test evidence tied to a configuration hash, audio evals,
> German UI localization, and frontend tests. `STUDIO_REFACTOR_PROPOSAL.md` is an earlier copy of
> sections 1–7 without the implementation plan.

## Direction

Rebuild Studio around a named customer agent with a persistent workspace. Use `simple_design.png` as inspiration for the spatial hierarchy: application navigation, contextual agent navigation, a focused editor, and persistent actions. Adapt the density to a small practice: fewer navigation items, readable labels, and a guided first setup.

The customer should always know which practice and agent they are editing, whether changes are saved, which revision is live, and which calendar a test can affect.

This extends the customer-readiness review in `../STUDIO_CUSTOMER_REVIEW_2026-09-06.md`. Its tenant isolation, global practice defaults, test/live confusion and ambiguous phone routing findings are prerequisites for a professional product.

## What the Wonderful implementation demonstrates

Local source root: `/Users/dimafadeev/Desktop/Catalog/work_projects/wonderful/repos/wonderful`. The observations below describe the inspected checkout, not a verified deployed Wonderful environment.

| Inspected source | Observed pattern | Application to Laufwise |
|---|---|---|
| `wonderful-ui/src/pages/AgentStudioV2/AgentStudioV2.tsx` | Nested agent routes share shell/breadcrumb context. | One persistent Studio layout; route files render focused sections. |
| `wonderful-ui/src/features/agents/pages/AgentStudioV2/components/AgentStudioV2Sidebar.tsx` | Sidebar, content and draft buffer share agent and revision context. | Explicit agent/revision selection for editing and tests. |
| `wonderful-ui/src/features/agents/pages/AgentStudioV2/hooks/useDraftAutosave.ts` | Serialized draft saves, visible save status and conflict callbacks. | Save drafts with optimistic concurrency; publishing is a distinct action. |
| `wonderful-ui/src/pages/AgentStudioV2/Agent/ChannelsTelephony/ChannelsTelephony.tsx` | Operational channels are separate from versioned draft editing. | Phone assignment, pause/resume and connection health have explicit operational actions. |
| `wonderful-controller/components/agents_v2/agent_config/service/test_data/valid_config_full.json` | Typed configuration separates profile, voice, recognition, synthesis and turn-taking settings. | A structured voice profile with customer presets and advanced settings. The file is a test fixture, not recommended production defaults. |
| `wonderful-controller/components/orchestrators/voice_orchestrator_factory/voice_orchestrator_factory.go` | Central ingress factory resolves tenant/workspace, full runtime agent, voice mode and interaction permission before startup. | Resolve and validate a complete runtime configuration once, identically for browser and phone entry points. |
| `wonderful-controller/components/orchestrators/latency_recorder/README.md` | One event-based timing subsystem derives call/turn latency. | Collect actual latency evidence instead of showing hardcoded model names as proof of quality. |
| `wonderful-agents/app/evaluation/engine/channels/voice/adapter.py` | Voice evaluations use audio preparation and a telephony WebSocket transport. | Extend text/tool evaluations with audio tests through the voice transport. |

Borrow these boundaries and interaction patterns. Retain Next.js, FastAPI, Pipecat and Laufwise's governed engine. Git branches, pull requests, multiple orchestrator implementations and a separate service for every concern would add substantial cost before solving this platform's immediate customer problems.

## 1. Replace the catalog-first UI with an agent workspace

Application navigation:

- Overview — agent readiness, recent outcomes, work requiring attention.
- Agents — named agents with practice, live/draft state and assigned number.
- Calls & callbacks — operational work and conversation history.
- Workflows — calendar imports, separate from voice editing.
- Connections — named practice accounts and health.
- Settings — organization, members and practice defaults.

Agent navigation:

- Overview
- Instructions
- Practice knowledge
- Capabilities
- Voice & language
- Tests
- Phone & handoff
- History

The header carries practice → agent breadcrumbs, live revision, draft save state, Test and Publish changes. Operational pages use their own relevant actions rather than suggesting that an account reconnection requires publishing a prompt.

Suggested desktop arrangement:

```text
Application rail | Practice / Receptionist      Draft saved   Test   Publish changes
                |-----------------------------------------------------------------
Agents          | Agent sections | Focused editor or overview
Calls           | Instructions   | Greeting
Workflows       | Knowledge      | Tone and language
Connections     | Capabilities   | Booking rules
Settings        | Voice          |
                | Tests          | Optional test drawer → transcript + actions
                | Phone          |
```

Use a narrow application rail and approximately 200–220px contextual navigation. Keep editor text within a readable width while allowing tables and call timelines to fill available space. On tablets collapse the application rail; on phones use drawers/section selectors and a full-screen tester. Verify at 375px, 768px and desktop widths.

**Status (2026-10-18): done, with a different application rail.** The rail is Agents, Run history, Dashboard, All workspaces and Governance (`frontend/src/components/studio/WorkspaceShell.tsx:28-37`). Workflows are listed as a kind of agent on Agents, calls and runs share Run history, and connections sit under Governance; `/studio/workflows`, `/studio/calls`, `/studio/connections` and `/runs` are redirects. Agent navigation is built as Build / Connect / Verify groups (`frontend/src/features/agents/AgentWorkspace.tsx:33-53`). "Practice knowledge" was renamed "Knowledge base" in #105. The rail collapses behind a toggle (`WorkspaceShell.tsx:109`). A rendered check at 375px/768px is unverified.

## 2. Establish a small, consistent design system

Use the reference's quiet neutral surfaces, white editor, subtle borders, clear selected navigation and generous grouping. Strengthen low-contrast secondary labels and keep practical input text readable. Use one primary action per context and reserve status colors for meaningful states.

Build reusable local components: AppShell, AgentShell, PageHeader, SettingsSection, FormField, SaveStatus, StatusBadge, EmptyState, ReadinessChecklist, ConnectionCard, ChangeReview and TestPanel. Use the existing Tailwind/token foundation. Consolidate duplicated button/input/error styles from `components/studio/ui.tsx` and pages.

Use ordinary proportional type for names and labels; reserve monospace for advanced IDs and logs. Every form has descriptions, inline validation, keyboard focus states, saving/error feedback and unsaved-change protection. Localize the customer interface for German practices; the language of the interface is separate from languages spoken by the agent.

**Status (2026-10-18): partly done.** The shell is `WorkspaceShell` with `StudioTrail`/`StudioActions` (`components/studio/WorkspaceShell.tsx`). Form primitives are `Section`/`Field` (`features/agents/Fields.tsx`) beside the older `components/studio/ui.tsx`, so the consolidation is unfinished. Readiness checklist, change review and test panel exist inline in `AgentWorkspace.tsx`, `insights.ts` and `VoiceTest.tsx`, not as separate shared components. Unsaved-change protection is built (`beforeunload`, `AgentWorkspace.tsx:207`). **Not done:** German UI localization. There is no i18n layer and the interface copy is English.

## 3. Separate agent identity, revisions and live activation

Proposed domain responsibilities:

| Entity | Responsibility |
|---|---|
| Agent | Stable tenant-owned identity: display name, practice reference, draft revision and published revision. |
| AgentRevision | Structured instructions, practice snapshot, voice settings, capability policy and pinned runbook/template versions. Mutable draft; immutable after publication. |
| Connection | Tenant-owned encrypted credentials and typed adapter configuration, with health and configuration generation. Secrets never enter revision content. |
| ChannelAssignment | Verified phone ownership, agent/revision activation target, real connection binding and paused/live state. |
| TestSession / Conversation | Exact agent revision, mode, connection configuration generation, channel and resulting evidence. |

Reuse `AgentInstance` as the pinned executable deployment where practical; introduce the stable parent/revision relationship rather than maintaining a second independent runtime. Keep the existing immutable governance templates. Customer behavior edits must not implicitly edit the enforced contract.

Draft lifecycle: edit → save → validate → publish immutable revision. Live activation is a separate, explicit switch of the channel's revision target; the publication flow can offer that switch after readiness passes. Existing calls retain the revision and resolved configuration captured when they started. Rollback selects a previous revision after validating it against current connection capabilities and practice configuration.

Use a revision counter/ETag and reject stale updates with a recoverable conflict response. Test results attach to a configuration hash. Editing the configuration makes old results visibly historical; they cannot silently certify the new revision.

**Status (2026-10-18): done, except the configuration hash.** `StudioAgent` holds `draft`, `generation` and `published_instance_id` (`backend/app/db/models.py:318`). `AgentInstance.snapshot_kind` marks test vs published snapshots. `VoiceChannel` has a unique `phone_number` and one channel per agent (`models.py:337`). A stale save returns 409 (`backend/app/agents/service.py:40`). Publish (`make_snapshot`) and activate (`activate`) are separate actions, and restore-to-draft and rollback-by-selecting-a-revision are in the History and Phone sections (`AgentWorkspace.tsx:772-816`). **Not built:** test results attached to a configuration hash.

## 4. Replace generic voice parameter forms with domain editors

Keep schema-rendered forms for technical templates and simple import parameters. The voice product needs purpose-built sections:

- Instructions: greeting, identity, tone, approved explanations and escalation wording.
- Practice knowledge: address, hours, services, duration, price and notification destinations.
- Capabilities: book appointments, answer practice questions, request callbacks; show unavailable operations with the reason.
- Voice: supported languages, previewable voice, pronunciation hints and tested conversation presets. Provider/model controls belong in Advanced.
- Phone & handoff: verified number, activation target, fallback behavior and staff destination.

Every offered control must alter the resolved runtime configuration. Do not add sliders for interruption or speed before the Pipecat integration supports and tests their effect.

Capabilities are the intersection of published policy and adapter support. A customer can disable supported capabilities; enabling a toggle cannot manufacture Thevea rescheduling support. Required identity checks and independent booking verification remain engine-enforced.

**Status (2026-10-18): done.** Domain editors live in `frontend/src/features/agents/ConfigEditor.tsx` over a typed `AgentConfig` (`backend/app/agents/config.py:44`). Capability toggles come from `GET /agents/capabilities`. The effective capabilities are the config intersected with what the bound calendar system supports (`capabilities.resolve`, `calendar.effective_config`, used in `backend/app/agents/runtime.py`). Pronunciation hints and voice preview are unverified.

## 5. Make Thevea a complete connection product

Connection setup must progress through explicit stages: credentials saved → access verified → calendar mapping verified → booking ready. A timeout yields unknown health, not a green connected state.

Add typed configuration for the nested room mapping, account labels, selected resources and relevant timezone. Discover actual rooms where the connector has a verified read operation; otherwise provide a staff-assisted mapping form and read-only verification. Do not invent endpoint support or room IDs.

Validate connection ownership on both binding and runtime resolution. Verify all allowed resources and read-back search rooms. Expose authenticated read-only readiness responses with actionable field-level problems. Connection updates increment a configuration generation so readiness/test evidence can be invalidated when the destination changes.

The UI says, for example, “Thevea — Practice calendar: access verified; 3 calendars mapped.” That state is derived from backend checks, never from the presence of a connection ID.

**Status (2026-10-18): partly done.** Done:
- The nested room mapping is typed and validated (`backend/app/schemas/connection.py`, `validate_config`).
- Systems come from a registry, `VOICE_CALENDARS`, exposed as `GET /connections/systems` (ADR-0014).
- Ownership is checked at binding (`api/v1/instances.py`, `service.owned_connection`) and again at runtime (`repo.get_connection(..., tenant_id)`).
- A read-only readiness check, `POST /agents/{id}/check`, names missing mappings (`service.check_calendar`).
- `POST /connections/{id}/preview` and `DELETE /connections/{id}` exist (#107).

**Not built:** a connection configuration update endpoint (a mapping change means a new connection), a configuration generation that invalidates evidence, and stored health stages with a last-check time. Room discovery is unverified.

## 6. Give browser tests and live phone calls one runtime preparation path

Add a domain service such as `prepare_voice_session(tenant, agent_id, revision_id, mode, channel)`. It resolves ownership, revision, practice, effective capabilities, provider settings, connection and notification routing, then produces a frozen per-session runtime configuration.

Both `api/v1/conversational.py` and `api/v1/telephony.py` use this service. The HTTP handlers remain transport adapters. `surface.py` receives the resolved configuration; it no longer discovers shared practice facts independently. The real-time dialogue keeps calling governed tools through the existing execution boundary.

Test modes are explicit:

- Rehearsal: synthetic calendar and isolated notifications, no production writes.
- Connection check: authenticated read-only verification, no model or booking write needed.
- Live acceptance test: clearly identified test account/calendar and effects, explicitly started by an authorized operator.

The test drawer stays attached to the selected revision. It displays transcript, proposed action, whether checks passed, booking read-back and notification result. “Book” plus a positive model response is not a passing result.

Add measured speech-end-to-audio-start latency and tool duration to call diagnostics. Audio evaluations should cover pauses, noise, interruptions, date/name corrections and a dropped call during a write. Preserve existing deterministic governance tests.

**Status (2026-10-18): done, except audio evals.** The service is `prepare_voice` / `admit_voice_call` / `open_voice_call` in `backend/app/agents/runtime.py`, called from both `api/v1/conversational.py` and `api/v1/telephony.py`. Live calls refuse the sandbox and unpublished snapshots. Test modes are `sandbox` / `read` / `write` (`conversational.py:46`, ADR-0018), and `write` needs explicit confirmation. Per-stage TTFB and tool `duration_ms` are recorded (`workloads/conversational/surface.py:359-395, 479`). **Not built:** audio-level evaluations. `evals.yml` replays text nightly.

## 7. Build an operational home for staff

Calls need durable deep links and a clear outcome above their transcript. Separate rehearsal sessions from live calls. Show verified bookings, callbacks, failures and notification delivery independently; “call completed” must not imply “booking succeeded.”

A callback is a persisted work item with owner, status and resolution. Existing task infrastructure can support this instead of adding a parallel inbox model. Connect real runs as advanced evidence under calls and imports; remove the sample Runs destination from customer navigation.

**Status (2026-10-18): done.**
- Calls deep-link with `?call=` (`features/history/CallsView.tsx:580`).
- A callback is a persisted `Task` (`backend/app/db/repo.py:834`), surfaced by `components/studio/Followups.tsx` and `POST /tasks/{id}/followup`.
- Run history reads real runs (`features/history/RunsView.tsx` → `GET /runs`), and `/runs` redirects there.

`SAMPLE_RUNS` remains only on the marketing landing page (`frontend/src/app/page.tsx:107`).

## Suggested code boundaries

```text
frontend/src/
  app/(workspace)/layout.tsx
  app/(workspace)/studio/agents/[agentId]/layout.tsx
  app/(workspace)/studio/agents/[agentId]/instructions/page.tsx
  app/(workspace)/studio/agents/[agentId]/knowledge/page.tsx
  app/(workspace)/studio/agents/[agentId]/capabilities/page.tsx
  app/(workspace)/studio/agents/[agentId]/voice/page.tsx
  app/(workspace)/studio/agents/[agentId]/tests/page.tsx
  app/(workspace)/studio/agents/[agentId]/phone/page.tsx
  features/agents/{components,data,hooks}
  features/connections/{components,data,hooks}
  features/calls/{components,data,hooks}
  features/workflows/{components,data,hooks}
  components/ui/

backend/app/
  agents/          # revisions, publication, readiness, runtime configuration
  practices/       # tenant-owned knowledge/configuration
  connections/     # ownership, typed configuration, verification
  workloads/conversational/  # Pipecat session, tools and recording
  api/v1/          # thin HTTP/transport boundaries
  db/              # persistence and migrations
```

This splits `studio/configure/[template]/page.tsx`, which currently mixes agent deployment, credential capture, imports and JSON execution testing. Keep feature state local and key fetched data by tenant plus agent/revision. An organization switch must discard the prior organization's editor/test context. Continue using one shared HTTP transport; avoid a global store duplicating server state.

**Status (2026-10-18): built with different paths.**
- **Frontend:** there is no `(workspace)` route group. The agent route is one catch-all, `frontend/src/app/studio/agents/[agentId]/[[...section]]/page.tsx` (plus `studio/agents/[agentId]/layout.tsx`, one level up), rendering `features/agents/AgentWorkspace.tsx`. Features are `features/agents/` and `features/history/` (calls + runs). Shared UI is still `components/studio/`, not `components/ui/`.
- **Backend:** `backend/app/agents/` exists (config, service, runtime, numbers, knowledge, practice_types). `practices/` was not created: practice facts live in `AgentConfig`, and workspace documents in `KnowledgeDocument`. `connections/` exists.
- `studio/configure/[template]/page.tsx` still exists for workflow templates. The deploy endpoint behind it now refuses phone numbers ("Assign verified phone numbers from the agent workspace", `backend/app/api/v1/instances.py`).
- Shared HTTP transport: `frontend/src/lib/api.ts`.

## Incremental migration

1. **Trust foundations:** close ownership gaps, validate phone assignments, isolate rehearsal, inject tenant practice configuration, fix typed Thevea mapping. Acceptance: two tenants cannot share bindings implicitly; live activation refuses sandbox/incomplete configuration.
2. **Agent identity and lifecycle:** map existing instances to stable agent records and revision snapshots. Preserve conversation IDs and history. Surface duplicate phone assignments for resolution rather than guessing which deployment is authoritative. Add update/concurrency semantics and explicit activation.
3. **New workspace:** introduce the shared shell and focused domain editors. Provide a setup checklist for new customers and direct editing for returning customers. Redirect old template URLs only when the target agent is unambiguous; otherwise show an agent picker.
4. **Test and publish loop:** add revision-specific rehearsal, readiness review, publish/activate and rollback. Acceptance: editing never changes current live behavior; tests identify exactly what they exercised.
5. **Operations and acceptance:** connect calls, callbacks, notifications and real run evidence. Observe a practice user connect an account, map calendars, configure an agent, rehearse, understand a failed check, activate, pause and resolve a callback without developer intervention.

The first deliverable should be one complete vertical path for a reception voice agent using Thevea. Its architecture should support additional agents, while its navigation exposes only capabilities customers can actually use.

**Status (2026-10-18):**
- **Steps 1–4: done.** The exception is test evidence tied to a configuration hash (§3).
- **Step 5: partly done.** Calls, callbacks and real runs are connected. The observed practice-user acceptance session is not recorded in the repo (unverified).

## Implementation plan and acceptance gates

Updated: 7 September 2026. This is the execution plan for the approved refactor. The visual design is original; Wonderful is a reference for information hierarchy and lifecycle boundaries, not a source to copy.

### Phase A — Customer configuration and safe ownership

- Define a validated, versioned voice configuration containing agent identity, practice knowledge, opening hours, treatments, allowed resources, language, greeting and notification destinations.
- Start new agents with empty customer identity and recipient fields; never inherit another practice's facts.
- Support structured Thevea room mappings and safe connection metadata in the API and UI.
- Validate tenant ownership and adapter compatibility at both binding and runtime boundaries.
- Preserve the existing governed booking engine and patient checks, including concurrent uncommitted booking/evaluation work.
- Acceptance: invalid schedules and room IDs are rejected; one tenant cannot bind another's account; a new draft contains no existing customer's contacts.
- **Status (2026-10-18): done.**
  - `AgentConfig` (`backend/app/agents/config.py`) starts with an empty `practice_name` and empty `recipients`.
  - Room mapping is validated in `schemas/connection.py`.
  - Binding is tenant-checked in `api/v1/instances.py` and `service.owned_connection`.
  - One leftover default: `resources` defaults to `["MA1","MA2","MA3"]`.

### Phase B — Persistent agent identity and immutable snapshots

- Add a tenant-owned stable agent with an editable draft and optimistic-concurrency generation.
- Reuse AgentInstance as the immutable executable behavior snapshot, labeled as test or published; retain the existing template version pin.
- Keep phone activation operational using a separately owned, unique verified number assignment.
- Save never publishes. Publish never silently activates. Draft tests create a pinned snapshot and cannot receive phone traffic.
- Provide history, restore-to-draft, explicit activation, and pause/resume through readiness checks.
- Acceptance: a stale save returns a conflict; published content cannot be edited; old calls retain their snapshot; duplicate number ownership fails safely.
- **Status (2026-10-18): done.**
  - The models are `StudioAgent` and `VoiceChannel` in `db/models.py`.
  - Stale saves return 409 (`service.check_generation`).
  - Number conflicts return 409 in `service.activate`, and a paused agent's number can move within one practice (#99).

### Phase C — One voice startup path

- Resolve the chosen snapshot, tenant, practice, capabilities and calendar before issuing media access.
- Browser rehearsal always uses isolated calendar and notification behavior. Connection checks are read-only. Production calls require a configured published snapshot and a real calendar.
- Pass the resolved configuration into prompt rendering, BookingSession, calendar construction and notifications.
- Make session media authorization single-use; retain and document any single-worker deployment constraint until shared ownership is implemented.
- Preserve independent post-write verification. Align Thevea verification reads with mapped calendars.
- Acceptance: testing a connected agent cannot create live appointments or email practice staff; live ingress refuses missing mappings; selected agent settings reach every runtime consumer.
- **Status (2026-10-18): done, with one decision since.** ADR-0018 (#101) now lets a Studio test use the real calendar, read-only or writing test bookings, but only by explicit choice; the sandbox remains the default. The single-worker constraint is obsolete: media tokens are single-use rows in `voice_call_token`, redeemed in one statement across replicas (`8ce1e6d`, `db/models.py:373`). Aligning Thevea verification reads with the mapped calendars is unverified.

### Phase D — Original Studio shell and focused editors

- Introduce reusable application navigation, contextual agent navigation, breadcrumbs, save status, status badges, form sections, readiness checklist, error states and empty states.
- Replace the catalog landing with named agents and a clear create action. Keep imports in Workflows and authoring under Advanced.
- Agent sections: Overview, Instructions, Practice knowledge, Capabilities, Voice & language, Tests, Phone & handoff, History.
- Make save/test/publish actions contextual and explicit. Show which revision is live and whether a saved draft differs.
- Keep all displayed values real; never populate metrics with sample values.
- Acceptance: every section is addressable by URL, keyboard accessible, and usable at 375px, tablet and desktop widths; every visible setting has a runtime effect.
- **Status (2026-10-18): done.** Sections are addressable at `/studio/agents/[agentId]/[section]`. The setup checklist became a clickable guide (#103). Accessibility and the 375px check are unverified.

### Phase E — Connections and operational workflows

- Provide named connections with explicit credential/access/mapping states and reconnect flow.
- Require real IDs for Thevea mapping; use manual verified mapping until a proven discovery endpoint is available.
- Verify phone ownership against the configured carrier account and expose exact webhook instructions. Do not provision paid numbers automatically.
- Keep existing calendar imports working and preserve their reports.
- Link directly to a saved conversation, label rehearsal/live mode, expose notification delivery and callback needs, and replace sample execution views with tenant-scoped persisted evidence.
- Acceptance: a customer can connect, map, check, test, activate, pause and find the resulting call without using an API console.
- **Status (2026-10-18): mostly done.** Named connections with mapping and removal are under `/studio/governance/connections`. Imports and their reports are preserved (latest: #120). Calls are labelled test or live and link to their follow-ups.
  - **Superseded:** "do not provision paid numbers automatically". ADR-0015 (#96) lets a practice claim a number from the platform's Twilio pool, and `activate` points that number's webhook at the backend itself (`backend/app/agents/numbers.py`).
  - **Not built:** an explicit reconnect flow that edits an existing connection.

### Phase F — Verification and rollout

- Unit/domain tests: schema invariants, tenant binding, draft concurrency, published immutability, capability restrictions and test/live separation.
- HTTP/repository tests: agent lifecycle, tenant isolation, number conflicts, history and error translation.
- Frontend checks: TypeScript/build, focused interaction coverage, rendered desktop/mobile review where the environment allows authenticated data access.
- Database: review forward migration; preserve historical instances and conversation references; do not auto-choose an owner for legacy duplicate phone assignments. Validate against an isolated database when available, never production test data.
- Voice acceptance: separately perform designated real-provider/audio scenarios for booking, busy slots, duplicate requests, provider outage, interrupted writes and notification delivery. Automated mocks are not live proof.
- Deliver a change summary with completed checks, migration requirements and any remaining external acceptance work. Do not claim production readiness based on compile/mock checks.
- **Status (2026-10-18): partly done.**
  - Backend domain and HTTP tests run in CI against a real Postgres (`docs/CICD.md`).
  - Frontend checks are typecheck and build only: there are no frontend tests.
  - Voice acceptance runs as nightly text replays (`.github/workflows/evals.yml`, `acc-*` scenarios). Audio-level scenarios are not built.

### Compatibility and rollout decisions

- Keep existing API routes available for workflows and historical records. Old voice configuration links route customers toward the new agent workspace rather than create duplicate phone deployments.
- Legacy instances are retained as history. Migration to a named agent requires explicit selection where multiple deployments exist; do not silently attach old contacts, credentials or numbers to new drafts.
- Draft configuration contains no credentials. Connection selection and phone assignment remain tenant-scoped operational state.
- No engine rewrite, provider migration, new microservice topology, repo-per-agent Git implementation or paid infrastructure provisioning is part of this refactor.
- Start with German/English/Russian/Arabic voice selection supported by the runtime. UI localization is a separate presentation concern; do not imply it changes the voice language.

### Work started before this document was requested

- Added initial AgentConfig validation and isolated practice conversion.
- Added regression tests for defaults, schedules, room mappings and runtime practice values; these four tests pass.
- Added initial ORM/migration/repository scaffolding for StudioAgent, immutable instance snapshots and VoiceChannel.
- Broadened connection configuration to accept validated nested room mappings.

These are implementation-in-progress notes, not a claim that the new lifecycle or UI is complete. The migration has not been applied to an external database.

**Status (2026-10-18): obsolete as a progress note.** All four items shipped in #80. Migrations now apply at app startup and pre-deploy (`backend/app/db/bootstrap.py`, `backend/railway.json`).
