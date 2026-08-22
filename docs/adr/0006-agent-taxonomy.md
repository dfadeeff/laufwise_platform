# ADR 0006 — The agent taxonomy: three tiers, split by who drives the sequence

- **Status:** Proposed (2026-08-22)
- **Deciders:** project owner + architecture session
- **Amends:** [0002](0002-studio-concept.md) D10 — `agent_class` gains a third value and is
  redefined. D10's *executor-binding* mechanism (one agent per run vs per-step `step.agent`) is
  unchanged and still correct; what changes is that binding is no longer the thing the field names.
- **Relates:** realizes PLATFORM_PLAN §4 (the three-peer use-case catalog), preserves §6.1 (two
  clocks); builds on [0004](0004-governed-calendar-import.md) D4 (orchestrator above the engine).

## Context

`agent_class` currently has two values (`backend/app/templates/contract.py`):

```python
AgentClass = Literal["conversational", "workflow"]
```

They come from ADR-0002 D10, which was answering a specific question: **how is an executor bound to
a step?** Conversational = one agent owns the whole run (no brain-swap mid-call, to protect the
real-time budget). Workflow = per-step executor via `step.agent`. That was the right answer to
that question, and it is still the right answer.

But the field is named `agent_class`, sits on the `Template` row, and is now read as *the taxonomy
of agents on this platform*. It is not one, and the gap shows in three places:

1. **PLATFORM_PLAN §4 lists three peers**, not two: conversational agents, **back-office task
   agents** ("governs *write actions* — blocking premature/unsafe writes behind approval +
   allowlist"), and document/workflow agents. The back-office peer has no class.
2. **Nothing in the repo currently plans a sequence.** `runbooks/calendar_import.yaml` carries
   `agent_class: workflow` and makes **zero model calls** — the orchestrator enumerates, the engine
   enforces, the connectors write. Every "agent" in the repo is a deterministic runbook. That is a
   virtue (deterministic, verifiable, free) but it means the field name currently overstates what
   exists.
3. **The two things `agent_class` conflates come apart the moment a model plans.** Executor binding
   and sequence authorship coincide today only because no model chooses a sequence. A back-office
   task agent has *per-step executors* (workflow-like) **and** a model choosing the order
   (conversational-like). `agent_class = workflow` cannot express it, so the tier PLATFORM_PLAN §4
   already promises is unrepresentable.

CLAUDE.md §XII's test — *"could you delete it and the engine still compiles and passes?"* — must
hold for whatever answers this. The engine's governed loop stays closed for modification.

## Decision

### D1 — Three tiers, split by **who drives the sequence**

Not by domain (customer-facing vs back-office), not by executor binding, and not by channel. The
discriminator is *what decides the next action*, because that is what changes the machinery:

| `agent_class` | Who drives the sequence | Clock | Executor binding (ADR-0002 D10, unchanged) |
|---|---|---|---|
| `workflow` | the authored contract — `steps[]`, in order, every run | durable, seconds | per-step (`step.agent`) |
| `task` | **a model, planning inside the governed loop** | durable, minutes–days | per-step |
| `conversational` | human turn ↔ model, in real time | real-time to speak, durable to act | one agent for the whole run |

A secondary axis — the clock — falls out of the first and is what makes the tiers un-mergeable: a
real-time surface cannot wait for a reviewer; a durable tier can wait for days.

**The framing that settles design arguments:** a `workflow` is *deterministic, with AI where you
want it* — an agent is a step you drop in where judgment is needed. A `task` or `conversational`
agent is *AI, with determinism where you need it*. laufwise's differentiator is that the
"determinism where you need it" is the existing governed loop, not a prompt.

`observer` and `assist` (watch-only tagging; operator copilot) are noted as future tiers and
**deliberately not added** — no runtime, no value (CLAUDE.md §III).

### D2 — The `task` tier lives **above** the engine, exactly where the orchestrator does

ADR-0004 D4 already established the pattern: `app/sync/orchestrator.py` sits above the engine,
enumerates a work-list, and runs one governed contract per item. **A task agent is that
orchestrator with a model choosing the work-list instead of an enumeration.**

This is the whole reason the tier is affordable. The engine is untouched; the task tier plugs in at
the existing `ExecutionAdapter` seam (`app/workloads/base.py`) and dispatches governed runs through
`Runtime.run_instance` like any other caller.

### D3 — A model never mutates lifecycle state; it proposes a transition

The model's only route to change a task's status is a **platform-owned protocol tool**
(`task_set_status`), whose result is then validated by a pure transition guard that refuses:

- an illegal transition (`pending → action_required` cannot happen);
- a pause without **both** a proposed action and a reason;
- a resume without **both** an approver and a timestamp;
- a decline without **both** a decliner and a timestamp.

When a capability is off (this agent may not pause), the enum value is **absent from the tool
schema** rather than rejected at runtime.

This is ADR-0004 D7's *"append-only is enforced by the absence of the method"* and ADR-0003 D4's
anti-fabrication rule, pointed at the **task lifecycle** instead of at domain state. It is the
condition on which the tier is allowed to exist: **the model proposes, the platform decides.**

### D4 — The task tier adds no third clock

PLATFORM_PLAN §6.1's durable path is already specified as *"approval gates that wait
minutes-to-days"*. A task agent's budget is the same budget. It is **native to the durable clock**,
not a new one. The two-clock separation is preserved unchanged, and the real-time path never learns
about tasks.

### D5 — A task agent's consequential actions are governed runbook runs

The model plans; the runbook enforces. A task agent does not get a private write path — its
consequential tools dispatch governed runs, so precondition → allowlist → approval → execute →
postcondition still stands between it and any system of record. Prompt-level policy is a hint; the
contract is the guarantee. Both get written, neither alone is trusted.

### D6 — `agent_class` gains `task` **in the same change that gives it a runtime**, not before

Adding the enum value now would let the publish gate accept a `task` template that nothing can
execute — a governance hole (a published contract with no enforcement path), not a feature. The
taxonomy is committed here; the `Literal` widens when `app/workloads/task/` exists.

When it lands, the publish gate gains per-class rules so cross-tier shapes are **unrepresentable
rather than documented as wrong**: a `workflow` template carrying a conversational surface is a
schema error; a `task` template without a declared trigger is a schema error.

### D7 — `Task` is a new persisted entity, and `ImportJob` is its ancestor

`ImportJob` (`app/db/models.py`) and `doctolib_login_jobs.py` are already proto-tasks: a durable
row, a status, a progress record, an error. When the tier lands, `ImportJob` converges into `Task`
with `task_type: calendar_import`. **That migration is the test of whether the tier is shaped
right** — if `ImportJob` does not fit, the model is wrong.

`Run.status` (`ok | blocked | rejected`) is an *outcome*, not a lifecycle. A task needs both. Do
not collapse them into one column; they answer different questions. Relatedly: **a declined
approval is a path, not a fault** — today `rejected` reads as an error, and it should not.

## Consequences

- **ADR-0002 D10 is narrowed, not overturned.** Executor binding stays as decided; it is simply no
  longer what `agent_class` names. D10's "workflow class" splits into `workflow` (authored
  sequence) and `task` (planned sequence), both keeping per-step executors.
- **`agent_class` is redefined on an existing field.** Published templates are immutable
  (ADR-0002 D15), so existing `workflow`/`conversational` rows keep their meaning — the value set
  grows, no row is reinterpreted. No migration.
- **New persisted entities when the tier lands:** `Task` (tenant-scoped, `task_type`,
  `trigger_type`, `status`, jsonb `context`, the approval fields) and `TaskEvent` (append-only,
  ordered — mirrors `EpisodeEvent`). Plus `Trigger` as an **operational** record, deliberately
  *outside* the versioned template: ops must be able to disable an entrypoint without a release,
  the same reason `Connection` is bound per-instance and not authored in the contract.
- **New seam, no engine change:** `app/workloads/task/` behind `ExecutionAdapter`, with the
  transition guard as a pure module (`app/tasks/state.py` — no DB, no FastAPI) so it is
  unit-testable the way `control_plane/runner.py` is.
- **The publish gate grows per-class branches.** This is the first time it becomes conditional on
  `agent_class`; keep the rules declarative, in `templates/validation.py`.
- **Frontend:** `AgentClass` in `frontend/src/types/index.ts` mirrors the backend and must widen in
  step. A task timeline is a third console surface beyond `/runs` and the Studio.
- **Documentation already landed** (PR #60): CLAUDE.md §XIII and seven skills in `.claude/skills/`
  carry the working detail. This ADR is the decision of record they describe.
- **Risk accepted:** naming a tier before building it invites building it prematurely. Mitigated by
  D6 — the enum value cannot exist without a runtime, so the taxonomy cannot leak into a publishable
  contract early.

## Open questions

- **Does `document/workflow agents` (PLATFORM_PLAN §4) need its own tier?** Current read: no — it is
  a `workflow`-class use case that proves read/verify rather than write. Revisit only if a document
  workload needs a model to choose its own sequence, at which point it is a `task`.
- **Where does model configuration live?** CLAUDE.md §XII defers the "model factory" to the agent
  layer. A `task` template must declare *something* (provider, model, budget) — is that a
  `parameters` entry, a new contract block, or instance-level config? Decide when building, not now.
- **Approval transport** for a task pause — reuses ADR-0002's still-open question (CLI vs webhook vs
  in-console queue). The task tier makes this urgent rather than theoretical.
- **Per-call tool approval with editable arguments.** `ApprovalDef` gates a *step*; a task agent
  wants to gate a *call* and let the approver correct the arguments before execution. Does
  `Approval` grow `proposed_args`/`final_args`, or is that a separate entity?
- **Does the conversational tier need multi-skill routing** (one active skill at a time, tools
  following the topic), or does the single-agent-per-run rule from ADR-0002 D10 make that moot?
