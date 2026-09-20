# Studio refactoring proposal

Date: 6 September 2026. Proposal only; product code has not been changed.

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

**Superseded 20 September 2026.** The rail as built has four items — Agents, Run history,
Dashboard, Governance — after reviewing Wonderful's own application rail. Workflows are a *kind of
agent*, listed on the Agents page beside voice receptionists rather than in their own rail item;
calls and workflow runs share Run history; connections and contracts sit under Governance.

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

## 2. Establish a small, consistent design system

Use the reference's quiet neutral surfaces, white editor, subtle borders, clear selected navigation and generous grouping. Strengthen low-contrast secondary labels and keep practical input text readable. Use one primary action per context and reserve status colors for meaningful states.

Build reusable local components: AppShell, AgentShell, PageHeader, SettingsSection, FormField, SaveStatus, StatusBadge, EmptyState, ReadinessChecklist, ConnectionCard, ChangeReview and TestPanel. Use the existing Tailwind/token foundation. Consolidate duplicated button/input/error styles from `components/studio/ui.tsx` and pages.

Use ordinary proportional type for names and labels; reserve monospace for advanced IDs and logs. Every form has descriptions, inline validation, keyboard focus states, saving/error feedback and unsaved-change protection. Localize the customer interface for German practices; the language of the interface is separate from languages spoken by the agent.

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

## 4. Replace generic voice parameter forms with domain editors

Keep schema-rendered forms for technical templates and simple import parameters. The voice product needs purpose-built sections:

- Instructions: greeting, identity, tone, approved explanations and escalation wording.
- Practice knowledge: address, hours, services, duration, price and notification destinations.
- Capabilities: book appointments, answer practice questions, request callbacks; show unavailable operations with the reason.
- Voice: supported languages, previewable voice, pronunciation hints and tested conversation presets. Provider/model controls belong in Advanced.
- Phone & handoff: verified number, activation target, fallback behavior and staff destination.

Every offered control must alter the resolved runtime configuration. Do not add sliders for interruption or speed before the Pipecat integration supports and tests their effect.

Capabilities are the intersection of published policy and adapter support. A customer can disable supported capabilities; enabling a toggle cannot manufacture Thevea rescheduling support. Required identity checks and independent booking verification remain engine-enforced.

## 5. Make Thevea a complete connection product

Connection setup must progress through explicit stages: credentials saved → access verified → calendar mapping verified → booking ready. A timeout yields unknown health, not a green connected state.

Add typed configuration for the nested room mapping, account labels, selected resources and relevant timezone. Discover actual rooms where the connector has a verified read operation; otherwise provide a staff-assisted mapping form and read-only verification. Do not invent endpoint support or room IDs.

Validate connection ownership on both binding and runtime resolution. Verify all allowed resources and read-back search rooms. Expose authenticated read-only readiness responses with actionable field-level problems. Connection updates increment a configuration generation so readiness/test evidence can be invalidated when the destination changes.

The UI says, for example, “Thevea — Practice calendar: access verified; 3 calendars mapped.” That state is derived from backend checks, never from the presence of a connection ID.

## 6. Give browser tests and live phone calls one runtime preparation path

Add a domain service such as `prepare_voice_session(tenant, agent_id, revision_id, mode, channel)`. It resolves ownership, revision, practice, effective capabilities, provider settings, connection and notification routing, then produces a frozen per-session runtime configuration.

Both `api/v1/conversational.py` and `api/v1/telephony.py` use this service. The HTTP handlers remain transport adapters. `surface.py` receives the resolved configuration; it no longer discovers shared practice facts independently. The real-time dialogue keeps calling governed tools through the existing execution boundary.

Test modes are explicit:

- Rehearsal: synthetic calendar and isolated notifications, no production writes.
- Connection check: authenticated read-only verification, no model or booking write needed.
- Live acceptance test: clearly identified test account/calendar and effects, explicitly started by an authorized operator.

The test drawer stays attached to the selected revision. It displays transcript, proposed action, whether checks passed, booking read-back and notification result. “Book” plus a positive model response is not a passing result.

Add measured speech-end-to-audio-start latency and tool duration to call diagnostics. Audio evaluations should cover pauses, noise, interruptions, date/name corrections and a dropped call during a write. Preserve existing deterministic governance tests.

## 7. Build an operational home for staff

Calls need durable deep links and a clear outcome above their transcript. Separate rehearsal sessions from live calls. Show verified bookings, callbacks, failures and notification delivery independently; “call completed” must not imply “booking succeeded.”

A callback is a persisted work item with owner, status and resolution. Existing task infrastructure can support this instead of adding a parallel inbox model. Connect real runs as advanced evidence under calls and imports; remove the sample Runs destination from customer navigation.

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

## Incremental migration

1. **Trust foundations:** close ownership gaps, validate phone assignments, isolate rehearsal, inject tenant practice configuration, fix typed Thevea mapping. Acceptance: two tenants cannot share bindings implicitly; live activation refuses sandbox/incomplete configuration.
2. **Agent identity and lifecycle:** map existing instances to stable agent records and revision snapshots. Preserve conversation IDs and history. Surface duplicate phone assignments for resolution rather than guessing which deployment is authoritative. Add update/concurrency semantics and explicit activation.
3. **New workspace:** introduce the shared shell and focused domain editors. Provide a setup checklist for new customers and direct editing for returning customers. Redirect old template URLs only when the target agent is unambiguous; otherwise show an agent picker.
4. **Test and publish loop:** add revision-specific rehearsal, readiness review, publish/activate and rollback. Acceptance: editing never changes current live behavior; tests identify exactly what they exercised.
5. **Operations and acceptance:** connect calls, callbacks, notifications and real run evidence. Observe a practice user connect an account, map calendars, configure an agent, rehearse, understand a failed check, activate, pause and resolve a callback without developer intervention.

The first deliverable should be one complete vertical path for a reception voice agent using Thevea. Its architecture should support additional agents, while its navigation exposes only capabilities customers can actually use.
