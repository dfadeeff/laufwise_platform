# ADR 0002 — Studio concept (two-tier authoring + configuration)

- **Status:** Accepted (2026-06-28)
- **Deciders:** project owner + grilling session
- **Supersedes/relates:** builds on [0001](0001-persistence-stack.md); realizes PLATFORM_PLAN §4 (use-case catalog), §6.1 (two clocks), §8 (templates/connectors)

## Context

The platform (`PLATFORM_PLAN.md`) is a *governed agent runtime*. The existing ontology in
`backend/` is: **Runbook** (process contract — ordered steps, per-step tool allowlist,
pre/postconditions, approval gates; `schemas/runbook.py`), **Workload/agent** (the executor
`ExecutionAdapter`; `workloads/base.py`), **Run** (`schemas/run.py`), **StateProvider**
(`providers/base.py`), **Principal** (`identity/base.py`). The only UI is a read-only `/runs`
operator console. There is no surface for users to compose or select agents.

The owner wants a **Studio**: "select agents and apply into their workflows," with the **first
use case being conversational voice agents**, using the existing Pipecat-based
[`voice_agents`](https://github.com/dfadeeff/voice_agents) repo as the agent backbone.

The phrase collides with the ontology: an agent (brain) is **inert without a runbook**, and the
runbook *is* the workflow. So "select an agent" is really "select/configure a bundle." Resolving
what that bundle is, who authors the governance contract, and how a voice surface plugs into the
durable runtime is what this ADR settles.

## Decision

Studio is a **two-tier** product. The same artifacts power both tiers; the tiers differ by who
uses them and what they're allowed to change.

| Tier | Who | Surface | Job |
|---|---|---|---|
| **Authoring** | technical (owner / laufwise / engineer) | structured **step-form editor** | create a **use-case template** = a runbook contract + a declared parameter schema |
| **Configuration** | **non-technical** domain expert / customer | **the Studio** | **select** a use case → **preconfigure** the agent → **deploy** a governed instance |

The non-negotiable framing: **the authoring tier authors the *cage*, not the *brain*.** The canvas
exists to make an *ungoverned* workflow impossible to express; it is not a free-form flow builder
(guards against PLATFORM_PLAN §8 risk #3 — drift into prompt-level guardrails). The end-to-end
**loop** (author a template → it appears in the catalog → a non-technical user configures it →
deploys a governed agent) is the v1 demo target.

### Key decisions (this session)

1. **Studio nature → authoring canvas**, reconciled into the two tiers above (a from-scratch
   non-technical canvas is impossible; "select & preconfigure" *is* the configuration tier).
2. **Authoring scope → author the cage, not the brain.** The canvas authors the process
   contract; agents are dropped into steps as opaque executors. Governance is **structural**:
   an enforced step cannot be saved without a tool allowlist + at least one verifiable condition.
3. **Persona → split.** Configuration tier = non-technical domain expert. Authoring tier =
   technical. (A single non-technical from-scratch author is not a real persona.)
4. **First use case → conversational voice agent**, `voice_agents` (Pipecat, FastAPI/WebSocket,
   swappable STT/LLM/TTS) as the backbone.
5. **v1 scope → both tiers.** Justified only because the *loop* is the demo; otherwise authoring
   a single hand-writable template would be over-build (CLAUDE.md §III/§X).
6. **Authoring form → structured step-form editor** (ordered list of step-forms), **not** a
   drag-drop graph canvas (kitchen-sink risk for one template) and not a raw YAML editor.
   Compiles **1:1 to the runbook YAML** — no new execution model.
7. **Two clocks → hybrid.** A runbook for a voice agent **mirrors conversation phases as
   trace-only steps and enforces at action steps.** Conversation flow stays on the real-time
   (Pipecat) clock; the durable engine governs only consequential actions.
8. **Hybrid mechanics → surface emits markers; engine owns enforced steps.** Phase steps are
   **never** driven by the durable engine — the surface emits lightweight trace events tagged
   with `run_id` into the **shared episode log**; only enforced steps dispatch to the engine.
   One log, two writers, **separate clocks**. Every step gains `kind: trace | enforced`.
9. **Knob handshake → template declares its own parameter schema.** Each template carries a
   `parameters` block (typed knobs + defaults + required flags) and `required_connections`. The
   configuration tier **auto-renders the form** from this schema. The schema *is* the tier-to-tier
   contract. Generalizes `voice_agents`' proposed `use_cases.json`.
10. **Executor binding → determined by agent *class* (template-level `agent_class`).**
    - **Conversational class:** ONE agent for the whole run — the surface owns the dialogue, to
      minimize real-time latency (no brain-swap mid-call). Enforced steps reference *tools* the
      surface invokes through the durable engine; there is no per-step executor. The first use
      case is this class, scoped to three jobs: **(a) clarification** (dialogue / `trace`),
      **(b) slot booking** (the enforced action), **(c) handoff** (escalation / approval gate).
    - **Workflow class (non-conversational, headless):** per-step agent — `step.agent` on each
      `enforced` step names that action's executor (tool / MCP / sub-agent). No real-time budget,
      so resolving multiple `ExecutionAdapter`s within one run is fine. **Not in v1.**

    `trace` steps never have an executor. (Supersedes the earlier "per-step executor is universal"
    framing.)
11. **Connections → real Google Calendar OAuth, per-tenant.** The first connector is a real
    Google Calendar `StateProvider` mapped as `has_free_slot`→`freebusy.query`,
    `book_appointment`→`events.insert`, `booking_confirmed`→`events.get` (read-back). Per-tenant
    OAuth via one laufwise Google client. **PHI/GDPR rule (non-negotiable):** calendar *content*
    is personal data — it is read transiently and **never persisted**; the episode log records
    only check *results* (booleans / minimal non-PHI fields), never raw calendar content, so the
    audit trail never becomes a PHI store (keeps ADR-0001 isolation intact).
12. **Token storage → encrypted in app Postgres (Supabase Vault).** Per-tenant access/refresh
    tokens stored encrypted on the `Connection` row (Vault/pgsodium or app-level envelope
    encryption); adapter refreshes on 401. Tokens are secrets, not PHI → consistent with ADR-0001.
13. **Instance & trigger → Twilio inbound telephony.** "Deploy" produces an `AgentInstance`
    (pinned `template@version` + filled params + bound connections + policy + status + assigned
    number). Each instance is assigned one Twilio number; an inbound call → API gateway resolves
    number→instance → starts a `Run` → spins up the Pipecat pipeline with that instance's config.
    The **media bridge (Twilio Media Streams ↔ Pipecat) lives in the FastAPI API/gateway tier**
    (§6.2); `book_appointment` dispatches to the durable engine — clocks stay separate. Single
    laufwise Twilio account in v1; per-tenant subaccounts deferred. Instance states:
    `draft → deployed → paused`.
14. **Validation (publish gate) → structural governance enforced.** A template is publishable
    only if: every `enforced` step has a tool allowlist + ≥1 verifiable condition; every
    referenced tool exists; every condition field resolves against the `required_connections`
    providers' query schemas; every parameter used in a condition/prompt is declared. This is
    what makes an ungoverned template *unrepresentable*.
15. **Versioning → immutable versions, instances pin, upgrades approval-gated.** Publishing
    creates an immutable version; editing forks a draft → publishes `v+1`. Each instance pins the
    version it deployed on; every `Run` records `template@version`. Upgrading an instance to a
    newer version is an explicit, **approval-gated** action (it changes a live governed contract,
    per §6.5).
16. **Data model → relational entities + JSONB contract body.** `Template` / `AgentInstance` /
    `Connection` / `Run` / `Approval` / `Tenant` / `User` are relational (FKs, `tenant_id`,
    status, indexes). The template's **contract** (ordered steps with `kind`/tools/conditions/
    approval) and its **parameter schema** are validated **JSONB** on the immutable Template-version
    row; the instance's **filled parameter values** are JSONB on `AgentInstance`. Mirrors
    runbook-is-data; immutability makes in-place step normalization unnecessary.

### Persisted shape (illustrative)

```
Template(id, name, version, agent_class, agent_surface, risk, status,
         contract JSONB, parameters JSONB)            -- immutable per (name, version)
AgentInstance(id, tenant_id, template_id, template_version,
              param_values JSONB, status, phone_number)
InstanceConnection(instance_id, connection_id, role)  -- binds required_connections
Connection(id, tenant_id, type, adapter, tokens_enc, scopes, expiry, config JSONB)
Run(id, instance_id, template_version, status, started_at, ended_at, trace_ref)
Approval(id, run_id, kind, status, requested_at, decided_by, decided_at)
```

### Resulting shape of a template (illustrative)

```yaml
template: praxis_appointment
version: 1
agent_class: conversational         # → one agent for the whole run (no per-step executor)
agent_surface: voice_agent          # the real-time conversational surface (one per template)
parameters:                          # → auto-rendered as the config-tier form
  llm:        { type: enum, options: [gpt-4o, qwen2.5:7b], default: gpt-4o }
  persona:    { type: text, required: true }
  locale:     { type: enum, options: [de, en], default: de }
  escalate_red_flags: { type: bool, default: true }
required_connections: [calendar]
steps:
  - id: greet            ; kind: trace
  - id: verify_patient   ; kind: enforced
    preconditions: [ "patient.exists == true", "consent.telephone_handling == true" ]
  - id: book_slot        ; kind: enforced   # conversational class → surface executes, no step.agent
    tools: [book_appointment]
    preconditions:  [ "calendar.has_free_slot == true" ]
    approval: { required_when: "type == 'urgent'" }
    postconditions: [ "calendar.booking_confirmed == true" ]
```

## Consequences

- **Runbook schema must be formalized.** Today `schemas/runbook.py` is thin (name/version/risk/
  step_ids). It must grow to the full contract above, including `step.kind`, per-step
  `agent`/`tools`/`pre`/`postconditions`/`approval`, the template `parameters` block, and
  `required_connections`. This is the prerequisite for both tiers.
- **`voice_agents` integration work:** its `conversation/policy.py` action-gating moves **into
  runbook preconditions** (governance leaves the surface); its `tools/registry.py` calls
  **dispatch to the durable engine** instead of executing locally; it emits **phase markers**
  into the shared episode log; its hardcoded law-firm flow is extracted to a use-case template.
- **Episode log is the single source of truth** for a run, written by two producers (surface +
  engine) keyed by `run_id`. It must accept both event kinds and preserve ordering.
- **New persisted entities** (extends 0001's "agent instances"): `Template` (versioned),
  `AgentInstance` (a template + filled parameters + bound connections + policy, tenant-scoped),
  `Connection` (a configured StateProvider). Runs reference the instance and the template version.
- **Two new frontend surfaces** beyond `/runs`: the authoring step-form editor and the
  configuration cockpit (catalog → parameter form → deploy).
- **Risk accepted:** both-tiers-in-v1 expands scope. Mitigated by: one use case, the
  conversational class only (single agent — per-step executors deferred to the workflow class),
  step-form (not graph) authoring, and a single built-in connection adapter for the demo.

## v1 scope summary

A full vertical slice (deliberately chosen "real" at each fork): both tiers · conversational
class only (single agent: clarify → book → handoff) · step-form authoring → runbook YAML · hybrid
trace/enforced steps on one episode log · real Google Calendar OAuth (per-tenant, Vault-encrypted
tokens) · Twilio inbound telephony with the media bridge in the API tier · immutable versioning
with approval-gated upgrades · relational + JSONB persistence on the ADR-0001 Supabase stack.

## Remaining unknowns (not yet decided)

- **OAuth/Twilio go-live ops:** Google sensitive-scope app verification and Twilio number
  provisioning/account setup are real-world gating steps, not architecture — track separately.
- **Approval transport for the handoff/upgrade gates:** CLI vs webhook vs in-console queue
  (PLATFORM_PLAN lists options; pick when building the gate).
- **Engine seam at v1:** start on `LocalEngine` (Phase 0/1) vs jump to Temporal — the durable
  path is invoked by telephony actions, so revisit against the §7 roadmap before building the
  enforced-step dispatch.
- **Policy object contents:** what "policy" on an `AgentInstance` holds beyond approval rules
  (rate limits, hours, allowed appointment types) — likely template-declared parameters, confirm.