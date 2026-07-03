# Studio — Staged Build Plan

> Implements **ADR-0002** (Studio two-tier authoring + configuration) on the **ADR-0001** stack.
> This is a build plan, not a new design — every decision is already settled in ADR-0002.
> Engineering rules: CLAUDE.md §III (smallest thing that works), §IV (surgical), §X (no kitchen-sink).

## Success criterion (the whole plan)

The **loop** is demoable end-to-end: a technical user authors a use-case template in the step-form
editor → it publishes (immutable version) and appears in the catalog → a non-technical user selects
it, fills the auto-rendered parameter form, binds a calendar connection, and deploys a governed
`AgentInstance` → an inbound phone call to that instance's number starts a governed `Run` that
**books an appointment through the durable engine and blocks an unsafe action mid-call**, with the
result visible in `/runs`.

---

## Strategic preconditions (what must be true for this to be a *defensible product*, not just good architecture)

The architecture is defensible *by design* (structural governance, prevention-before-action,
grounded checks, audit). The **business** defensibility is not proven by shipping code — it rests
on three things the runtime alone does not give you. Treat them as **gates on the expensive
stages**, not as blockers on the cheap ones.

| # | Precondition | Why it matters | Gates |
|---|---|---|---|
| **P1** | **A named design partner** (one praxis / clinic / firm that feels the pain). | A horizontal "governed runtime" is sold by the workload that adopts it. Building real OAuth + telephony for a hypothetical buyer is the classic infra trap. | Stages **5–7** |
| **P2** | **An owner + decision for the speech/action governance gap.** Governance bites on *actions* (tool calls vs. state); for a voice agent the dangerous output is often *words* (bad medical/legal advice). "No medical advice" is currently a prompt-level concern in the surface — the exact soft-guardrail PLATFORM_PLAN §8 says is *not* the moat. | The safety claim is the wedge; a voice demo that calls itself "safe" while speech is ungoverned is a credibility risk. Decide whether output-side checks (or a "speech-act" enforced step) are in scope. | Stage **6** (before the surface represents itself as safe) |
| **P3** | **A connector thesis beyond N=1.** The moat is *connectors + compliance trust + a live workload*, not the runtime code. v1 ships one connector (Google Calendar). | "We have N hard, audited connectors and the trust to be the system-of-record for what agents may do" is the durable moat; have a roadmap past one. | Stage **5+** (informs, doesn't block) |

**Build-order consequence (decided):** Stages **1–4 proceed unconditionally** — they prove the
*mechanism* (author → configure → deploy → governed run) against a **simulated** connector and a
chat surface, with **zero external dependencies**, so they are the cheapest possible test and
de-risk everything. Stages **5–7** (real Google OAuth, voice surface, Twilio) are **gated on
P1–P2**. The two external tracks (G, T) may *start their lead-time clocks* in parallel, but the
*integration code* waits on a design partner and the speech-safety decision.

---

## Ground truth read from the code (so stages quote real seams)

**laufwise engine (sibling `../laufwise`, installed `pip install -e`)** — domain-agnostic, keep it so:
- `RunbookSpec(runbook, version, risk, state: dict[str,StateBinding], steps: list[StepSpec])` — `spec/models.py`
- `StepSpec(id, description, preconditions: list[CheckSpec], tools: list[str], approval: ApprovalSpec|None, execute: ExecuteSpec|None, postconditions, on_fail)` — **no `kind`, no `agent`**
- `CheckSpec(expr=alias "check", reason=alias "else")`; `StateBinding(provider="memory", query)`
- `LocalEngine(provider, evaluator, trace, approval, adapter)` → `run_step(spec, step)` / `run(spec)`
- `StepResult(step_id, status, reason, expr, blocked_tool, state_hash)`; `StepStatus = OK|BLOCK|REJECT`
- `StateProvider.query(name, params=None) -> StateView`; `StateView.{exists,count,contains_all,get_field,value}`
- `ExecutionAdapter.execute(step, allowlist) -> StepOutcome`; `StubAdapter`, `SimulatedAdapter`, `ToolNotAllowed`
- `ApprovalGate.request(step) -> Decision(approved, note)`; `AutoApprovalGate`
- `JsonlTraceSink(path).event(**fields)`; `load_runbook(path) -> RunbookSpec`
- `BuiltinEvaluator` DSL: `binding.field op value` / `binding.count op value` / `binding.method(args)` / bare-truthy; `py:` refused.

**Platform backend (`backend/app`)** — facade + thin DTOs, **no DB yet**:
- `control_plane/runtime.py`: `Runtime` facade, `run()` raises `RuntimeNotConfiguredError` (Phase 0 stub), lazy-imports laufwise.
- `schemas/runbook.py` (thin: name/version/risk/step_count/step_ids), `schemas/run.py` (`RunRequest{runbook,case}`, `RunResult`).
- `api/v1/{runs,runbooks,approvals,health}.py` + `router.py`; `api/deps.py` (`get_runtime` singleton).
- `providers/memory.py` (`MemoryStateProvider`, returns `dict` — **note seam mismatch**, see Flags).
- `workloads/conversational/__init__.py` (empty placeholder); `observability/trace.py` (`otlp_configured()`).

**Frontend (`frontend/src`)**: only `/runs` console, reads `lib/sample-runs.ts` fixture; `types/index.ts` mirrors DTOs.

### Two gaps between ADR-0002's template and the laufwise engine (down-compile across them — do NOT bloat the engine)
1. ADR adds `kind: trace|enforced`, `agent_class`, `agent_surface`, `parameters`, `required_connections`.
   These are **Studio/template** concepts, not engine concepts. Keep them platform-side; the compiler
   feeds the engine a `RunbookSpec` of **enforced steps only** (trace steps never reach the engine —
   ADR decision #8). This preserves "the engine knows no domain specifics" (SKILL invariant #2).
2. ADR's `approval.required_when: "type == 'urgent'"` is a **field expression**; laufwise v0 only does
   `risk >= <level>`. Closed in Stage 1 by evaluating the field expression in the compiler/dispatch
   layer (it's already a pure check over the same state) — **not** by special-casing the engine.

---

## Engine recommendation: LocalEngine for all of v1 (do NOT pull in Temporal)

ADR-0002 leaves this open ("revisit against the §7 roadmap before building the enforced-step dispatch").
Recommendation: **LocalEngine + Postgres `DurableStore`** for the entire v1 loop. Reasoning, grounded:

- The conversational class has exactly **one** enforced action (`book_slot`) plus one optional
  approval gate (urgent). The enforced-step dispatch from telephony is a **seconds-long synchronous
  call** (calendar freebusy → insert → read-back). If the process dies mid-call the *call itself*
  drops — there is nothing for Temporal's durable retry to resume. The two-clocks rule (PLATFORM_PLAN
  §6.1) already keeps the engine **off** the audio path; that is the real requirement, and LocalEngine
  satisfies it.
- The only durable wait in v1 is the **upgrade-approval gate** (§6.5) — low-volume, out-of-band,
  control-plane. A DB-backed `Approval` row + in-console queue covers it without an orchestrator.
- Temporal is **PLATFORM_PLAN Phase 2** and brings its own Postgres + worker tier + Railway deploy.
  Adopting it for v1 violates the staged-infra rule (SKILL invariant; §6 intro) — heavy infra with no
  workload demanding it yet.
- **Trigger to revisit Temporal (name it now):** when an enforced step needs *durable retries* across
  a restart, OR an approval wait must *survive a deploy* at volume, OR the **workflow class** (per-step
  executors, multi-hour runs) ships. None are in v1. The `Engine` seam stays clean so the swap is a
  one-line binding change in `runtime.py` when that day comes.

---

## Staged infra — when each heavy store becomes necessary

| Infra | Introduced at | Trigger (why it's needed exactly then) |
|---|---|---|
| Filesystem YAML + `JsonlTraceSink` | Stage 1 | Run a hand-written template through the engine; zero external deps. |
| **App Postgres (Supabase, ADR-0001)** | **Stage 2** | First moment you must persist **immutable template versions + tenant-scoped instances/connections** and a shared episode log keyed by `run_id`. JSONL can't do versioning/RLS/joins. |
| **Langfuse (OTLP)** | Optional, Stage 2+ | When cross-run audit/replay UI is wanted. `observability/trace.py` already gates on `OTEL_EXPORTER_OTLP_ENDPOINT`; swap `JsonlTraceSink`→OTLP sink. Not required for the demo. |
| **Temporal Cloud + worker** | **Not in v1** | See recommendation above — PLATFORM_PLAN Phase 2. |
| **PHI store** | **Never (by rule)** | Calendar content is read transiently and never persisted (ADR-0002 #11). The episode log records only check *results*. |

---

## External-dependency critical path (parallel non-code tracks — start Day 1)

These gate the *live* demo but are **not code**; they have long lead times, so kick them off in
parallel with Stage 1, not when Stages 5/7 begin.

- **Track G — Google sensitive-scope app verification.** One laufwise Google Cloud project; OAuth
  consent screen; `calendar` + `calendar.freebusy` scopes are **sensitive/restricted** → Google
  review can take weeks. *Unblocks Stage 5.* Mitigation: develop Stage 5 against test users (unverified
  app allows ≤100 test users) so code lands before verification clears.
- **Track T — Twilio number provisioning.** One laufwise Twilio account; buy a voice-capable number;
  configure inbound webhook → Media Streams; for German PII consider a DE/EU number (regulatory).
  *Unblocks Stage 7.* Mitigation: Stage 6 (surface) is testable over a local WebSocket with no Twilio.

Both tracks run while Stages 1–4 build the loop with a **simulated** connector + chat surface, so the
software loop is provable before either external dependency clears.

---

## Stages (dependency-ordered)

### Stage 1 — Formalize the contract + wire LocalEngine (the prerequisite)
**Maps to PLATFORM_PLAN Phase 0.** ADR-0002 Consequences: "Runbook schema must be formalized … the
prerequisite for both tiers."

- **Deliverable:** A platform-side **template contract model** (the full ADR shape: per-step
  `kind`/`tools`/`pre`/`postconditions`/`approval`, top-level `agent_class`/`agent_surface`/
  `parameters`/`required_connections`) + a **compiler** that down-compiles the enforced steps to a
  laufwise `RunbookSpec`, and `Runtime.run` driving `LocalEngine` over it. Templates loaded from
  filesystem YAML for now.
- **Create:**
  - `backend/app/templates/contract.py` — Pydantic models: `TemplateContract`, `StepDef(kind: Literal["trace","enforced"], ...)`, `ParameterSpec`, mirroring ADR's "Resulting shape of a template".
  - `backend/app/templates/compiler.py` — `to_runbook_spec(contract, param_values) -> laufwise.RunbookSpec` (filters `kind=="enforced"`, substitutes parameters into checks/prompts, evaluates field-expr `required_when`).
  - `backend/app/control_plane/factory.py` — builds `LocalEngine(provider, BuiltinEvaluator, JsonlTraceSink, AutoApprovalGate, SimulatedAdapter)` from a contract + case.
  - `backend/tests/test_runtime_localengine.py`.
  - `runbooks/praxis_appointment.yaml` (the ADR §"Resulting shape" template, hand-written).
- **Modify:**
  - `backend/app/control_plane/runtime.py` — implement `run()` (replace the `RuntimeNotConfiguredError` stub) and `list_runbooks()` (read the templates dir).
  - `backend/app/providers/memory.py` — conform to laufwise `StateProvider` (`query → StateView`), resolving the seam mismatch (see Flags #1).
  - `backend/pyproject.toml` — uncomment/activate the editable `laufwise` dep.
- **Seam:** `Engine` (LocalEngine), `StateProvider`, `ExecutionAdapter`, `CheckEvaluator`, `TraceSink`.
- **Done when:** `POST /api/v1/runs` with the praxis template + a no-consent case **BLOCKs** `book_slot`
  and returns the failing expression (`consent.telephone_handling == true`); the same template with a
  free slot returns `OK`; both appear in `/runs` (still fixture-backed UI is fine). A trace JSONL lands in `runs/`.
- **NOT yet:** no DB, no auth, no real connector, no UI editor, no telephony. `trace`-kind steps are
  parsed and stored but not executed (they're surface markers — Stage 6).

### Stage 2 — Persistence + relational entities (ADR-0001 stack)
**Maps to PLATFORM_PLAN Phase 1 (App Postgres).** ADR-0002 #16 + Persisted shape.

- **Deliverable:** The ADR-0002 relational schema on async SQLAlchemy 2.0 + asyncpg + Alembic against
  Supabase EU Postgres (direct conn 5432). Templates/instances/runs move from filesystem to DB; the
  episode log becomes a `run_id`-keyed table (the "one log, two writers" substrate).
- **Create:**
  - `backend/app/db/__init__.py`, `backend/app/db/session.py` (async engine/session), `backend/app/db/base.py`.
  - `backend/app/db/models.py` — `Tenant`, `User`, `Template`(immutable per `(name,version)`, `contract` + `parameters` JSONB), `AgentInstance`(`param_values` JSONB, `status`, `phone_number`), `Connection`(`tokens_enc`, `scopes`, `config` JSONB), `InstanceConnection`, `Run`(`template_version`, `trace_ref`), `Approval`, `EpisodeEvent`(`run_id`, `seq`, `kind`, payload JSONB).
  - `backend/alembic.ini`, `backend/migrations/` (Alembic env using the **direct** 5432 URL).
  - Repository helpers `backend/app/db/repo.py`.
- **Modify:**
  - `backend/app/config.py` — add `database_url` (+ direct-conn note from ADR-0001).
  - `backend/app/control_plane/runtime.py` — load templates/instances from repo, not filesystem.
  - `backend/app/observability/trace.py` — add a Postgres `EpisodeEvent` sink alongside JSONL (and the optional OTLP→Langfuse swap, gated by env).
- **Seam:** `DurableStore` (Postgres), `TraceSink` (DB episode log + optional Langfuse).
- **Done when:** a published template + a deployed instance survive a process restart; a `Run` row +
  ordered `EpisodeEvent` rows are written for a run; `GET /api/v1/runs` reads them from Postgres. A
  crashed run's prior events remain queryable (foundation for replay).
- **NOT yet:** Supabase Auth/RLS enforcement (Phase 4), Temporal's own Postgres, Langfuse mandatory.

### Stage 3 — Authoring tier: step-form editor + publish gate + versioning
**Maps to PLATFORM_PLAN Phase 8 (templates), demoed early per ADR §"v1 scope".** ADR-0002 #2, #6, #14, #15.

- **Deliverable:** The structured **step-form editor** (ordered list of step-forms, **not** a graph,
  **not** raw YAML) that compiles 1:1 to the contract JSONB; a **publish/validation gate** that makes
  an ungoverned template *unrepresentable*; immutable versioning (edit forks a draft → publishes v+1).
- **Create:**
  - `frontend/src/app/studio/author/page.tsx` + `frontend/src/components/studio/StepForm.tsx`, `ParameterSchemaForm.tsx`.
  - `backend/app/api/v1/templates.py` — `GET /templates`, `GET /templates/{name}`, `POST /templates` (draft), `POST /templates/{name}/publish`.
  - `backend/app/templates/validation.py` — the publish gate.
  - `backend/tests/test_publish_gate.py`.
- **Modify:** `backend/app/api/v1/router.py` (mount templates); `frontend/src/types/index.ts` (template/contract types); `frontend/src/lib/api.ts`.
- **Seam:** none new (writes `Template` rows; reuses the contract model from Stage 1).
- **Done when (Done-when style):** publishing a template **rejects** with a precise message when an
  `enforced` step has no tool allowlist OR no verifiable condition, references a missing tool, uses a
  condition field that doesn't resolve against a `required_connections` provider's query schema, or
  uses an undeclared parameter; a valid template publishes as an **immutable** `v1`; editing it
  produces a `draft` and publishes `v2` while `v1` stays byte-identical.
- **NOT yet:** workflow-class authoring (`step.agent`), drag-drop graph, collaborative editing,
  template marketplace/sharing across tenants.

### Stage 4 — Configuration tier: catalog → parameter form → deploy
**Maps to PLATFORM_PLAN Phase 8.** ADR-0002 #3, #9, #13 (instance creation only).

- **Deliverable:** The non-technical **Studio cockpit**: browse the catalog → select a use case →
  fill the form **auto-rendered from the template's `parameters` schema** → bind `required_connections`
  → **deploy** a governed `AgentInstance` (`draft → deployed`). This closes the **software loop**.
- **Create:**
  - `frontend/src/app/studio/page.tsx` (catalog), `frontend/src/app/studio/configure/[template]/page.tsx`.
  - `backend/app/api/v1/instances.py` — `POST /instances` (deploy), `GET /instances`, `POST /instances/{id}/pause`.
  - `backend/app/instances/deploy.py` — validates `param_values` against the template's parameter schema; pins `template@version`.
- **Modify:** `router.py`; `frontend/src/lib/api.ts`; `frontend/src/types/index.ts`.
- **Seam:** none new (writes `AgentInstance` + `InstanceConnection`).
- **Done when:** a non-technical user, with no code, deploys an instance whose params validate against
  the schema; the instance pins `template@version`; a manually-triggered run of that instance executes
  the governed contract (against the **simulated** connector) and shows in `/runs`. **At this point the
  full loop is demoable without Google or Twilio.**
- **NOT yet:** real connector (Stage 5), telephony trigger (Stage 7), approval-gated **upgrade** of a
  live instance to a newer version (Stage 8).

### Stage 5 — Google Calendar Connection (real OAuth, Vault tokens, PHI scrub)
**Maps to PLATFORM_PLAN Phase 3 (StateProvider) / blocked by external Track G.** ADR-0002 #11, #12.

- **Deliverable:** A real Google Calendar `StateProvider` + per-tenant OAuth connection flow with
  Vault-encrypted tokens, replacing the simulated adapter for the `calendar` connection.
- **Create:**
  - `backend/app/providers/calendar.py` — laufwise `StateProvider`: `has_free_slot`→`freebusy.query`, `book_appointment`→`events.insert`, `booking_confirmed`→`events.get` (read-back). Refresh on 401.
  - `backend/app/connections/google_oauth.py` — per-tenant OAuth (one laufwise client); `backend/app/connections/crypto.py` — Vault/pgsodium envelope encryption for `tokens_enc`.
  - `backend/app/api/v1/connections.py` — `POST /connections/google/start`, OAuth callback, `GET /connections`.
  - `backend/tests/test_calendar_provider.py` (mocked Google API) + a **PHI-scrub** test.
- **Modify:** `control_plane/factory.py` (bind the real provider when a connection is present); `config.py` (Google client id/secret, Vault key).
- **Seam:** `StateProvider` (real SoR), `Connection` entity, encryption.
- **Done when:** an instance with a bound Google connection runs `book_slot` end-to-end against a real
  test calendar — `has_free_slot` reflects real freebusy, the event is inserted, `booking_confirmed`
  verifies via read-back; the episode log contains **only** booleans/minimal non-PHI fields (verified
  by the scrub test) — **no raw calendar content is ever persisted**; a 401 triggers a refresh.
- **NOT yet:** other connectors (PVS/FHIR, ERP), per-tenant Google clients, calendar write
  compensation/cancellation flows.

### Stage 6 — Conversational surface (two clocks): voice_agents extraction + engine dispatch
**Maps to PLATFORM_PLAN Phase 3.** ADR-0002 #4, #7, #8, #10 (conversational class).

- **Deliverable:** The Pipecat surface mounted on the API tier's WebSocket; its governance moves into
  the runbook; consequential actions dispatch to the durable engine; phase markers + engine events
  share one `run_id`-keyed episode log. Scoped to three jobs: **clarify (trace) → book (enforced) →
  handoff (approval/escalation)**.
- **Create:**
  - `backend/app/workloads/conversational/surface.py` — Pipecat pipeline (swappable STT/LLM/TTS via env), one agent for the whole run.
  - `backend/app/workloads/conversational/dispatch.py` — surface tool calls (`book_appointment`) → `Runtime.run_step` (durable clock); audio stays responsive ("let me check…").
  - `backend/app/workloads/conversational/markers.py` — emit phase trace events into the shared episode log keyed by `run_id`.
  - `backend/app/api/v1/ws.py` — WebSocket transport endpoint.
- **Modify:** `control_plane/runtime.py` (expose single-step dispatch for the surface); episode-log writer to accept both event kinds while preserving order (Stage 2's `EpisodeEvent.seq`).
- **Seam:** the **two clocks** (real-time Pipecat vs durable engine); `TraceSink` (two writers, one log).
- **Done when:** a chat/voice session over the WebSocket books an appointment **through the governed
  engine**, and an unsafe action (no consent / no free slot) is **blocked mid-conversation** with the
  surface voicing the block reason; the episode log interleaves phase markers (trace) and the enforced
  `book_slot` event in correct order under one `run_id`. The audio loop never blocks on the engine.
- **NOT yet:** Twilio/telephony (Stage 7) — tested over a raw WebSocket here; barge-in tuning,
  multi-language beyond de/en, brain-swap mid-call (forbidden by #10 for this class).

### Stage 7 — Twilio inbound telephony + media bridge in the API tier
**Maps to PLATFORM_PLAN Phase 3 / blocked by external Track T.** ADR-0002 #13.

- **Deliverable:** An inbound Twilio call → API gateway resolves **number → instance** → starts a
  `Run` → spins up the Pipecat pipeline with that instance's config. The **media bridge (Twilio Media
  Streams ↔ Pipecat) lives in the FastAPI API tier**; `book_appointment` still dispatches to the
  durable engine (clocks stay separate).
- **Create:**
  - `backend/app/api/v1/telephony.py` — Twilio inbound webhook + Media Streams WS bridge.
  - `backend/app/telephony/bridge.py` — Twilio audio ↔ Pipecat transport.
  - `backend/app/telephony/resolve.py` — `phone_number → AgentInstance`.
- **Modify:** `api/v1/router.py`; `config.py` (Twilio account/number); deploy flow (Stage 4) to assign a number on `deployed`.
- **Seam:** API/gateway tier transport only — **engine stays off the audio path** (PLATFORM_PLAN §6.2).
- **Done when:** a real phone call to the instance's Twilio number books an appointment through the
  governed runtime, and an unsafe request is blocked mid-call; the `Run` + episode log are visible in
  `/runs`. The bridge runs in the API tier; the engine is never on the audio path.
- **NOT yet:** per-tenant Twilio subaccounts (single laufwise account in v1), outbound calling,
  SMS/WhatsApp surfaces, call recording storage.

### Stage 8 — Approval gates: handoff escalation + approval-gated version upgrade
**Maps to PLATFORM_PLAN §6.5 / Phase 3.** ADR-0002 #10(c), #15.

- **Deliverable:** Two real approval gates replacing the auto-approve stub: (a) the **handoff**
  escalation gate in the conversational flow, and (b) the **approval-gated upgrade** of a live instance
  to a newer template version. Transport = DB-backed in-console queue.
- **Create:**
  - `backend/app/approval/gate.py` — `ApprovalGate` impl writing/reading `Approval` rows (replaces `AutoApprovalGate`).
  - `frontend/src/app/runs/approvals/page.tsx` — operator approval queue (the `/runs` console already lists an "Approvals" tab).
  - `backend/app/instances/upgrade.py` — re-pin `template@version` behind an approval.
- **Modify:** `api/v1/approvals.py` (currently a stub returning `[]`) → list/resolve pending; `control_plane/factory.py` (bind the DB gate); the urgent-booking `required_when` path from Stage 1.
- **Seam:** `ApprovalGate` (human-in-the-loop), `Approval` entity.
- **Done when:** an urgent booking **pauses** for approval and resolves from the console (proceed/halt);
  upgrading a deployed instance to `v2` is **refused** until an operator approves, after which new runs
  pin `v2` and in-flight runs keep `v1`.
- **NOT yet:** webhook/CLI approval transports (in-console only for v1), SLA/timeout policies on
  pending approvals, the self-improvement proposal pipeline (PLATFORM_PLAN Phase 7).

---

## Risk & sequencing note

- **Build first to de-risk:** Stage 1 (engine wiring) — it proves the contract→engine seam with **zero
  external dependencies** and is the prerequisite for everything. The single largest unknown is the
  **down-compile** (template contract → laufwise `RunbookSpec`, incl. trace-step filtering and
  field-expr approvals); land it before any UI.
- **Prove the loop before the integrations:** Stages 1→4 deliver the full author→configure→deploy→run
  loop against the **simulated** `SimulatedAdapter` and a chat surface. This is the demo-able milestone;
  it lets Tracks G/T (Google verification, Twilio provisioning) run in parallel without blocking.
- **What can be stubbed:** the connector (Stage 5 simulated until Track G clears), telephony (Stage 7
  WebSocket-only until Track T clears), approvals (`AutoApprovalGate` until Stage 8).
- **Biggest unknowns / watch items:** (1) Google sensitive-scope verification lead time (external,
  start Day 1); (2) two-writers-one-log ordering under concurrency (Stage 6) — `EpisodeEvent.seq` must
  be authoritative; (3) PHI-scrub correctness (Stage 5) — make it a hard test, not a code comment;
  (4) field-expression approval evaluation (Stage 1) reusing the pure check evaluator, not a second
  expression engine.

## Flags — places ADR-0002 is ambiguous or collides with code (resolved minimally, not invented)

1. **`StateProvider` seam mismatch.** Platform `providers/base.py`/`memory.py` returns `dict`; laufwise's
   returns `StateView`. The engine consumes laufwise's. **Resolution:** conform platform providers to
   laufwise's `query(name, params=None) -> StateView` (Stage 1). Flagging because it's a real, silent
   incompatibility today.
2. **`kind` placement.** ADR says "every step gains `kind`" but also "compiles 1:1 to runbook YAML, no
   new execution model." **Resolution:** `kind` lives in the *platform* contract; the compiler emits an
   enforced-only `RunbookSpec` to the engine. Keeps laufwise domain-agnostic. Flag if the intent was to
   teach the engine about `kind`.
3. **Approval `required_when` expressivity.** ADR template uses `type == 'urgent'`; laufwise v0 only does
   `risk >= level`. **Resolution:** evaluate the field expression in the platform dispatch layer via the
   same `BuiltinEvaluator` (pure check), not by extending the engine's approval logic.
4. **"Policy" object on `AgentInstance`** (ADR Remaining Unknowns). **Resolution for v1:** model policy as
   template-declared `parameters` + filled `param_values`; defer a separate policy object until a knob
   (rate limit / hours / allowed types) can't be expressed as a parameter.
5. **Approval transport** (ADR Remaining Unknowns). **Resolution for v1:** DB-backed in-console queue,
   reusing the existing `/approvals` endpoint + console "Approvals" tab; webhook/CLI deferred.