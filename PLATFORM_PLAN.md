# Laufwise Platform — Plan

> **Thesis.** A *governed agent runtime*: the runtime that lets you run **any agent** inside an
> enforced, auditable **process contract** grounded in your real systems of record. The platform
> is the product; conversational, task, and document agents are workloads that run on it.
>
> **Core sentence.** Others ship agents. We ship the runtime that decides what an agent is
> allowed to do — and proves it did only that.

Framing reference: the "$100B AI infrastructure for agents" thesis — the workers (agents) exist,
the *workplace* (infra) does not. Named layers: Memory, Identity, Payments, Communication,
Security/Observability. We do not build all five; we own the control plane and pull the rest in
as workloads demand. The agents are the proof the infra is needed — the way apps proved AWS,
Stripe, and Twilio were needed; the platform is the durable business.

**Positioning: horizontal-first.** The product is sold and shaped as a horizontal primitive —
any agent, any developer, any domain. The buyer is the team building agents, and the value is the
same everywhere: run an agent inside an enforced, auditable contract grounded in real state. We
lead with the platform, not a vertical. Regulated/operational domains (healthcare, finance, legal)
are the *sharpest demonstrations* of why the platform matters — they appear as worked examples and
reference workloads, not as the go-to-market wedge. The conversational praxis agent (§5) is a
proof, not a product line.

---

## 1. The platform — what it gives every agent

The platform is the laufwise control plane. It knows nothing about the domain on top of it.
Every action any agent takes passes through the same enforced loop:

```
precond (vs real state) → tool allowlist → approval gate →
execute via adapter → postcond (vs real state) → checkpoint + trace → next step
```

What that buys any workload, regardless of surface:

| Capability | What it means for an agent | laufwise seam |
|---|---|---|
| **Process contracts** | The agent can only do what the runbook permits, in order. | spec + CheckEvaluator |
| **Grounding in a system of record** | Pre/postconditions verify against real state, not the model's claim. | StateProvider |
| **Tool confinement** | Only allowlisted tools are callable; everything else is refused. | ExecutionAdapter + tool gate |
| **Human-in-the-loop** | Risky actions block on approval (CLI / webhook / queue). | ApprovalGate |
| **Audit & replay** | Every run is an append-only episode log + OTEL spans; replayable. | TraceSink + DurableStore |
| **Bring-your-own-agent** | Wraps LangGraph, raw LLM, MCP — the agent is a plugin. | ExecutionAdapter |

The differentiator vs everything in the LangSmith/Langfuse ecosystem: that ecosystem is
*post-hoc* (traces, evals, annotation — after the action). The platform operates **before** the
action. **Prevention, not detection.** We feed their detection; we are the layer underneath it.

## 2. Where the 5 infra layers fit

| Layer | Stance | Why |
|---|---|---|
| **Security / Observability / Governance** | **OWN.** = laufwise. The wedge. | Built, rarest, prevention-grade, and the gating requirement for serious buyers. |
| **Identity** | **Minimal.** Tenant + actor (agent acting-as) + permission/consent scope. Reuse OIDC. | Any multi-tenant deployment needs it; don't reinvent it. |
| **Memory** | **Thin, scoped.** Continuity built from the episode logs we already emit. | "Agent remembers prior runs" — but don't become a memory company. |
| **Communication (multi-agent)** | **Adopt standards (MCP, A2A).** Already wrapped via ExecutionAdapter. | Real but premature. Adopt protocols, don't build a network. |
| **Payments (agent wallets)** | **Defer.** | No near-term workload requires it. |

Platform = **Governance (own) + Identity (minimal) + Memory (scoped)**, with Comms/Payments as
future optionality.

## 3. Architecture

```
WORKLOADS (agents — peers, plug into the same runtime)
  Conversational      Back-office task     Document / workflow      (future
  agents (voice/chat) agents               agents                    use cases)
        │                  │                    │                       │
        └──────────────────┴── every action passes through ▼ ──────────┘
THE PLATFORM = laufwise control plane
  precond → tool allowlist → approval → execute → postcond → checkpoint + trace
  Seams: Engine · StateProvider · ExecutionAdapter · DurableStore · TraceSink · ApprovalGate · CheckEvaluator
        │              │                │                  │
   IDENTITY        MEMORY          STATE (SoR)        OBSERV / AUDIT
   tenant/actor/   continuity      ERP/CRM/DB/        OTEL→Langfuse +
   consent scope   from episodes   FHIR/calendar/API  episode log + replay
```

**Invariant that makes the platform horizontal:** the workload never calls tools directly. A
conversational agent's `book_appointment`, a task agent's `create_vendor_draft`, a document
agent's `flag_paragraph` — all route through a runbook step. The runtime, not the agent, decides
whether the action is permitted and confirms it landed in the system of record. Swap the runbook
(data) and the surface (plugin); the runtime is unchanged. That is the test of a primitive.

## 4. The use-case catalog (peers on one runtime)

Each is a workload; none is the platform. Each proves a different half of the guarantee.

| Use case | Surface | What it proves about the platform |
|---|---|---|
| **Conversational agents** (voice/chat) | STT→LLM→TTS or chat | The runtime governs a *real-time, human-facing* workload — blocking/escalating mid-conversation. (Worked example in §5.) |
| **Back-office task agents** | headless / API | The runtime governs *write actions* — blocking premature/unsafe writes behind approval + allowlist (vendor onboarding pattern). |
| **Document / workflow agents** | headless / batch | The runtime governs *read/verify* work — postconditions ground every claim in a source (insolvency corpus already in laufwise `use_cases/`: "every flag cites a real document and a valid statute"). |

One control plane · swappable surfaces · swappable runbooks → many workloads from one primitive.

## 5. Worked example — a conversational agent on the platform

Chosen because it is the most demanding workload (real-time, human-facing, regulated), so if the
runtime governs *this* cleanly it governs anything. Concretely: an inbound voice agent for a
medical praxis (the law-firm voice_agents pattern, retargeted).

Agent jobs: appointment booking grounded in the calendar/PVS; triage intake with **red-flag
escalation** to a human; prescription-refill requests routed to doctor approval (never
auto-issued); insurance capture with read-back; hard boundary of **no medical advice**.

The runbook *is* the product — same contract format as every other workload:

```yaml
runbook: praxis_appointment
risk: medium
state:
  patient:  { provider: pvs,      query: "patients/by_kvnr/{kvnr}" }
  calendar: { provider: calendar, query: "slots?date={date}&type={type}" }
  consent:  { provider: pvs,      query: "patients/{id}/consent" }
steps:
  - id: verify_patient
    preconditions:
      - check: patient.exists == true
      - check: consent.telephone_handling == true   # consent to process
  - id: triage_safety
    preconditions:
      - check: py:triage.no_red_flag_symptoms
        else: "red_flag -> escalate to human immediately"
    on_fail: halt
  - id: book_slot
    preconditions:
      - check: calendar.has_free_slot == true
    tools: [book_appointment]
    approval: { required_when: "type == 'urgent'" }
    postconditions:
      - check: calendar.booking_confirmed == true   # verified in PVS, not the bot's word
    on_fail: halt
```

The surface is the voice_agents stack (FastAPI + Pipecat, cascaded STT→LLM→TTS); the governance
is the platform. The point of the example is not "we built a voice bot" — it is "the platform
governs even a live phone call."

## 6. System design & deployment architecture

The conceptual layers above say *what* the platform guarantees. This section says *how it runs* —
the runtime topology, datastores, transports, and deploy targets. The decisions here are
deliberately staged: adopt the heavy infrastructure only when a workload demands it, never on day
one (laufwise §7 — "default to SQLite/JSONL, make Temporal/Langfuse adapters").

### 6.1 Two clocks — the load-bearing separation

A governed agent runtime has **two execution paths with incompatible latency budgets**. Conflating
them is the central architectural mistake to avoid.

| Clock | What runs here | Budget | Mechanism |
|---|---|---|---|
| **Real-time path** | The conversational turn loop: STT → LLM → TTS, VAD, barge-in. | sub-second | Pipecat, in-process, over **WebSocket**. Never touches Temporal. |
| **Durable path** | The runbook itself: pre/postconditions, tool execution, approval gates that wait minutes-to-days, checkpoints. | seconds-to-days | The control-plane engine (LocalEngine → **TemporalEngine**). |

The conversational surface *speaks* in the real-time clock and *acts* through the durable clock:
when the voice agent decides to `book_appointment`, the audio loop stays responsive ("let me check
that for you…") while the action is dispatched to the durable engine, which enforces the contract
and returns an outcome the surface then voices. **Temporal orchestrates the process, not the
audio.**

### 6.2 Execution tiers

```
                 ┌─────────────────────────────────────────────┐
   WebSocket ───▶│  API / Gateway tier  (FastAPI)              │
   (voice/chat)  │  • REST control-plane API                  │
                 │  • WS transport for conversational surface │
                 │  • auth, tenant resolution, request DTOs   │
                 └───────────────┬─────────────────────────────┘
                                 │ dispatch run / signal approval
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  Execution tier  (Temporal workers)         │
                 │  • drives the runbook contract loop         │
                 │  • each StateProvider.query / execute =     │
                 │    a Temporal activity (all I/O here)       │
                 │  • approval gate = durable wait_condition   │
                 └──────┬───────────────┬──────────────┬───────┘
                        │               │              │
                 StateProviders     TraceSink      Inference
                 (SoR adapters)    (OTEL→Langfuse) (managed STT/LLM/TTS)
```

- **API tier never runs the agent loop inline.** It accepts a request, starts or signals a workflow,
  and streams results back. This keeps the web process responsive and the runtime crash-safe.
- **Execution tier is where laufwise's engine lives.** Start with `LocalEngine` + a Postgres
  `DurableStore`; cut over to `TemporalEngine` (same `Engine` interface) when a workflow genuinely
  needs durable retries / long approval waits. The interface swap is the whole point of the seam.
- **Inference is offloaded, not co-located.** STT/LLM/TTS run on managed APIs (e.g.
  Deepgram / a hosted LLM / Cartesia) or dedicated GPU infra — *not* on the app host. Local
  Whisper/Piper are for dev only (voice_agents caps at ~2 concurrent calls by design). This is the
  single biggest driver of cost and latency; decide the provider per environment.

### 6.3 Datastores — deliberate split

| Store | Holds | Why separate |
|---|---|---|
| **App Postgres (Supabase)** | Tenants, users, runbooks, run metadata, approvals, **non-PHI** app data. Supabase **Auth** also covers B2B staff login (the Identity layer, cheaply). | Managed Postgres + auth + storage in one; good DX. |
| **Temporal's Postgres** | Workflow event history (the durable run state). | Temporal wants direct, unpooled Postgres; do not point it at Supabase's pooled/RLS instance. |
| **Trace store** | OTEL spans / episode logs for audit, replay, and process mining. | OTLP → Langfuse (or ClickHouse at scale). Orthogonal to durability (laufwise §1.4). |
| **PHI store (when a regulated workload needs it)** | Patient identifiers, clinical data. | **Region-locked (EU), access-controlled, DPA/BAA posture.** Do **not** put PHI in Supabase casually; keep it isolated and let app Postgres hold only non-PHI references. |

### 6.4 Deploy topology (Railway-first)

- **Railway** hosts the stateless tiers and managed Postgres: the **FastAPI API/gateway**, the
  **Temporal worker** service, the **Next.js** frontend, and **app Postgres** (or Supabase as the
  managed alternative).
- **Temporal Cloud** for the orchestration backend — do **not** self-host a Temporal cluster on
  Railway (stateful, multi-service, fights the platform). Workers on Railway connect out to
  Temporal Cloud.
- **Managed inference** for STT/LLM/TTS, called from the workers/surface.
- **Langfuse** (cloud or self-host) as the OTLP trace sink.

This keeps everything on Railway *except* the two things Railway is bad at — a stateful Temporal
cluster and GPU inference — which become managed dependencies.

### 6.5 Self-improving agents — gated proposals, never autonomous contract edits

The episode log (`(step_id, state_hash, decision, tool_calls, outcome)`) is the raw material for a
closed improvement loop. The hard boundary: **improvement produces *proposals*; promoting a
proposal to a live contract version always passes through the approval gate.** The agent never
edits its own enforced contract autonomously — that would delete the very guarantee the platform
sells (laufwise §1.5: enforcement is structural/authoritative; improvement is
semantic/probabilistic).

```
production traces ─▶ mine (which steps block, which postconditions reject, where state is missing)
                 ─▶ PROPOSE runbook/eval changes + auto-generated regression evals
                 ─▶ HUMAN review (approval gate)  ◀── the non-negotiable step
                 ─▶ new, versioned contract  ─▶ back into the runtime
```

Design implications to honor from day one: episode logs are append-only and schema-stable;
runbooks are **versioned** (every run records the contract version it ran under); contract changes
ship through the same approval/audit machinery as risky actions. Build the proposal pipeline early;
build autonomous self-modification never.

## 7. Roadmap (each phase independently demoable)

Each phase names the **infra step** it lands (tying §6 to the build), not just the feature.

| Phase | Deliverable | Infra step (from §6) | Done when |
|---|---|---|---|
| **0 — Prove the runtime governs a run** | Bind `control_plane/runtime.py` to laufwise's `LocalEngine`; route a runbook through the API; surface BLOCK/REJECT in the `/runs` console. | LocalEngine in-process (no Temporal, no DB yet — JSONL trace). | `POST /api/v1/runs` **blocks** a no-slot/no-consent step and **explains why**; it shows in the console. |
| **1 — Durable state + audit** | Postgres `DurableStore`; episode log + `rh replay`; TraceSink → Langfuse via OTLP. | App **Postgres (Supabase)**; **Langfuse** trace sink. Still LocalEngine. | A crashed run resumes; any run is replayable and visible in Langfuse. |
| **2 — Execution tier (Temporal)** | Move the contract loop into a **Temporal worker**; cut `LocalEngine → TemporalEngine` (same `Engine` seam); approval gate = durable `wait_condition`. | **Temporal Cloud** + worker service; Temporal's **own Postgres**. | The API only starts/signals workflows; a multi-hour approval wait survives a deploy. |
| **3 — Conversational surface (two clocks)** | Mount the Pipecat **WebSocket** surface; its tool calls dispatch to the durable engine while audio stays responsive; **managed inference** (STT/LLM/TTS). | WS transport in API tier; inference offloaded off-host. | A live call books an appointment through the governed runtime; unsafe action blocked mid-call. |
| **4 — Identity & permission scope** | Tenant + actor (agent acting-as) + consent/permission scope. | **Supabase Auth** for B2B login; PHI kept out of app Postgres. | Multi-tenant, permission-gated execution. |
| **5 — Scoped memory** | Cross-run continuity from versioned episode logs. | Reads existing trace/episode store. | Returning-user / resumable experience. |
| **6 — Second + third workload** | Add the back-office task and document agents on the same runtime. | No new infra — proves horizontality. | Three workloads, one control plane. |
| **7 — Self-improvement loop** | Mine episode logs → propose runbook/eval changes → **human approval** → versioned contract. | Proposal pipeline over the trace store; runbook versioning. | A production-mined improvement ships through the gate (never autonomously). |
| **8 — Hosted control plane (paid/closed)** | Run history, audit reports, templates, connectors, process mining. | Full **Railway + Temporal Cloud + Supabase + Langfuse** topology. | The platform business. |

**Deploy milestone:** the first push to **Railway** (API + worker + Next.js + Postgres) lands
alongside Phase 2, when there's a worker tier worth deploying. Phases 0–1 run entirely locally.

## 8. Moat & risks

**Moat:** (1) prevention, not detection; (2) grounded in the system of record — the hallucination
firewall; (3) auditable + replayable by construction; (4) horizontal primitive (runbook is data,
agent is a plugin).

**Risks:** (1) StateProvider/SoR integration is the real engineering — backends are messy and
closed; make "state unavailable" a first-class blocking outcome, not a crash. (2) Keep the check
evaluator a *pure function of state*, or the wedge blurs. (3) Don't drift from a *process*
contract into prompt-level guardrails — the spec stays process-shaped (steps, pre/postconditions,
approvals). (4) Don't build all five infra layers; own the control plane, adopt or defer the rest.