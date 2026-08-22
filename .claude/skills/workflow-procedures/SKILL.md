---
name: workflow-procedures
description: The deterministic tier — trigger-driven step graphs, durable execution across waits and approvals, and how an agent becomes one step inside a graph. Read before adding a runbook/template, changing the engine loop, or designing anything that must survive a restart.
---

# Workflow procedures — the deterministic tier

This is the tier laufwise already is. `runbooks/*.yaml` + `control_plane/runner.execute_contract`
+ the laufwise engine loop *is* a procedure runtime, and a stricter one than most. This skill is
about the two things it does not have yet: **durability** and **composition with agents**.

## The pitch, and why it is the inverse of an agent

> **Deterministic, with AI where you want it.** The flow is predictable. Agents are first-class
> steps you drop in exactly where judgment is needed, not a wrapper around the whole thing.

Every capability the platform already has — connectors, tools, providers, agents — is an island.
The procedure is the connective tissue that lets one trigger pull them into a single flow.

## Anatomy

| Part | Rule |
|---|---|
| **Trigger** | exactly **one** per procedure — manual, webhook, or schedule |
| **Steps** | a graph, run in the order you connect them |
| **Data flow** | any step reads the trigger payload or an earlier step's output by variable |
| **Runs** | every fire is recorded with a step-by-step trace |

laufwise's `StepDef` (`app/templates/contract.py`) is `kind: trace | enforced` with
`preconditions`, `tools`, `approval`, `execute`, `postconditions`, `on_fail`. That step is **richer
per-step** than most — it carries its own governance contract — but the *catalogue* is narrower.
The step kinds a mature procedure tier needs and laufwise lacks: **branch**, **wait**, **use an
LLM**, and **dispatch to an agent**.

## Durable execution — the property to build first

A run should not have to finish in one pass. While parked, a run is **persisted rather than held
in memory**, so it resumes exactly where it left off even if the platform restarts.

Three ways to park:

| Mode | Resumes when | Use for |
|---|---|---|
| **Duration** | the configured time passes | a known pause |
| **Condition** | a stored row's field matches a value | another system is the source of truth |
| **Callback** | an external system POSTs the step's callback URL | that system knows when it's done |

Details that are hard-won and worth getting right the first time:

- The callback URL is **stable per step, not per run** — it contains no run id, so it can be
  configured once in the external system. The run is identified by a run id handed out at trigger
  time and echoed back by the caller.
- A callback arriving *before* the run reaches the wait gets **409 Conflict** (retry later); a run
  that can no longer reach that wait gets **410 Gone**. Repeating an accepted callback is
  **idempotent** and keeps the first payload.
- A condition wait that matches **multiple rows fails the run** rather than picking one. Only
  fields protected by a single-column PK or unique index may be selected. *Ambiguity is an error,
  never a guess* — the same rule as `StateUnavailable` → BLOCK.
- A safety net reclaims runs whose timer was lost, marks a run stuck mid-step **interrupted** rather
  than letting it hang, and **expires unattended waits** after a bounded period. An immortal
  suspended run is a leak.

### Run states

`queued` · `running` · `suspended` · `succeeded` · `failed` · `cancelled` · `interrupted`

laufwise's `Run.status` is `ok | blocked | rejected` — an *outcome*, with no lifecycle. A durable
tier needs both: a lifecycle state **and** the governance outcome. Do not collapse them into one
column; they answer different questions.

**And the rule everyone gets wrong:** *rejecting an approval is not a failure.* The run takes the
rejected path and can still finish successfully. laufwise's `rejected` is currently an
error-flavoured overall status — it should be a **path**, not a fault.

## Composition — an agent is a step

This is the seam that makes the whole platform agentic, and its asymmetry is the important part:

| Step dispatches to… | What happens to the run |
|---|---|
| a **conversational** agent | answers inline; the run **never pauses** |
| a **task** agent | the work becomes a task; the run **suspends** until the task finishes, then resumes with the result |

So `suspended` covers three things uniformly: a wait, a human approval, and a task agent. One
durability mechanism, three uses.

It composes the other way too: any caller that can send an HTTP request — including a tool an agent
calls — can fire a webhook-triggered procedure. **That is exactly what laufwise should offer: a
task agent whose consequential tools are governed runbook runs.** The model plans; the runbook
enforces.

## Triggers

| Trigger | Payload |
|---|---|
| **Manual** | whatever you provide; also the API entrypoint |
| **Webhook** | the JSON body; protected by a shared secret header |
| **Schedule** | empty payload + the scheduled time; pausing disarms, publishing re-arms |

Two security details worth copying: a wrong-secret request must be **indistinguishable from an
unknown procedure**, so the URL cannot be probed; and a webhook or schedule trigger fires **only
once the procedure is published** — a draft can only be fired by an explicit Test.

Programmatic invocation is a per-trigger opt-in: manual allows it by default, webhook and schedule
require turning it on and republishing. A programmatic call does **not** emulate webhook auth or
alter the schedule.

## Drafts, publish, and "test runs are real"

- You always edit a **draft**; the published version keeps running untouched.
- **Publishing is what makes it active.**
- **A test run executes for real** — it calls connectors, writes to systems, and dispatches agent
  tasks just like a live run. Say this out loud in the UI. A safe-looking Test button that writes to
  production is a trap, and the honest fix is the warning plus test resources, not a fake dry-run
  mode that drifts from the real path.

laufwise's publish gate + immutable published versions (the seed skips an existing `(name, version)`)
already match this. Keep it.

## What to add, in order

1. **Run lifecycle state** alongside the governance outcome (`Run.status`).
2. **`wait` and `branch` step kinds** in `StepDef` — the two the engine cannot express today.
   `on_fail` already hints at branching; make it real.
3. **Durable suspend/resume**: persist run position, resume by id. Prerequisite for *both* a
   non-blocking approval gate and the task tier.
4. **`agent` step kind** — dispatch to a task agent, suspend, resume with its result.
   `StepDef.agent` already exists as a field with no runtime behind it.
5. **Triggers on instances** (webhook/schedule), replacing "call the API and wait".
6. Reframe **`rejected`** from fault to path.

Each is a seam addition. **None of them changes the engine's governed loop** — precondition →
allowlist → approval → execute → postcondition → trace stays closed for modification (§XII).

## Rules

1. One trigger per procedure. Multiple entrypoints ⇒ multiple procedures, or a task agent.
2. Ambiguous resume state fails the run. Never resolve ambiguity by choosing.
3. Parked state is persisted, never in-memory. If a restart loses it, it was not durable.
4. A rejected approval is a path, not a failure.
5. Unattended waits expire.
6. A test run is a real run. Label it, don't fake it.
7. Published is immutable; edit a draft, bump the version.
