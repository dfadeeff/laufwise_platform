---
name: agent-taxonomy
description: The vocabulary for what kind of agent is being built — workflow procedure, task agent, or conversational agent — and which laufwise seam each one plugs into. Read this FIRST whenever a request says "agent" without saying which kind, before adding an `agent_class` value, a new template mode, or a new runtime surface.
---

# Agent taxonomy — what kind of thing are you actually building?

The single most expensive mistake on an agent platform is treating "agent" as one noun. It is
several runtimes with different clocks, different failure modes, and different review surfaces.
Make the distinction a **first-class value on the record** and refuse to let the schema mix them.

## The two axes that actually matter

Every "agent" question resolves once you answer two things:

| Axis | Question | Values |
|---|---|---|
| **Who drives the loop** | What decides the next action? | a human turn · a model's plan · a fixed graph you authored |
| **What the clock is** | How long may a step take? | real-time (sub-second, a person is waiting) · async (minutes–days, a task) · triggered (an event, then done) |

Everything else — channels, tools, prompts — is downstream of those two.

## The three tiers

| Tier | Loop driver | Clock | Output goes to | Distinctive machinery |
|---|---|---|---|---|
| **workflow** (a *procedure*) | the authored contract | triggered, seconds | a run record | the governed loop: precondition → allowlist → approval → execute → postcondition → trace |
| **task** (async agent) | a model's plan | async, minutes–days | a task timeline | task state machine, triggers, approvals, protocol tools |
| **conversational** | human turn ↔ model | real-time | the customer | turn-taking, interrupts, STT/TTS, skill switching |

Two later modes exist in the wider design space and should **not** be built before there is a
runtime for them (§III): an **observer** (watches a human-handled conversation, tags it, never
participates) and an **assist** agent (rides along on a live call as a copilot for the *operator*,
never speaking to the customer). Note them, do not add the enum values yet.

### The schema must reject cross-tier fields

A task agent with a voice block is not a warning, it is a schema error. A conversational template
with a schedule trigger is a schema error. **Make the wrong shape unrepresentable rather than
documenting that it is wrong** — the publish gate is where this belongs (§0, fail-closed).

## The framing that settles most design arguments

> **A procedure is deterministic, with AI where you want it.**
> An agent is a *step* you drop in exactly where judgment is needed, not a wrapper around the whole thing.
>
> **An agent is AI, with determinism where you need it.**
> Instructions and tools; the model picks the path.

Learn that inversion. A procedure with an agent step is a different animal from an agent with a
procedure tool, and confusing them produces designs that are neither reviewable nor useful.

## Where laufwise stands today

`backend/app/templates/contract.py` has a two-value enum:

```python
AgentClass = Literal["conversational", "workflow"]
```

Which is under-specified. The reality:

| laufwise artefact | What it really is |
|---|---|
| `runbooks/calendar_import.yaml` (`agent_class: workflow`) | a deterministic governed graph, zero model calls — a **procedure** |
| `runbooks/praxis_appointment.yaml` (`agent_class: conversational`) | a procedure with `kind: trace` dialogue markers |
| `app/workloads/conversational/` | an empty placeholder |
| — | a **task tier** — not built, and this is the gap |
| `app/sync/orchestrator.py` | a procedure fanning out over a work-list |

**The honest reading: laufwise today has no agents. It has an excellent governed procedure
engine.** Every "agent" in the repo is a deterministic runbook. That is not a criticism — the
governed loop is the hard part and it is built. But the thing that makes a platform *agentic* — a
model that plans a sequence rather than executing an authored one — is the missing tier.

### The three-value taxonomy to move to

| `agent_class` | Loop driver | laufwise runtime | Status |
|---|---|---|---|
| `workflow` | the authored contract (`steps[]`) | `control_plane/runner.execute_contract` | **built** |
| `task` | a model, planning inside the governed loop | needs a Task tier: state machine + triggers + protocol tools | **the gap** |
| `conversational` | human turn ↔ model, real-time | `workloads/conversational/` + a session tier | placeholder |

Leave the field an **open enum** on the `Template` row rather than a two-valued `Literal`, so
adding a tier is a data change and a publish-gate rule, not a migration.

## The rule that keeps all three honest

A model must never change lifecycle state by returning a value. Its only route is a
**platform-owned protocol tool** whose result is then validated by a state machine: illegal
transitions refused, a pause refused unless it carries a proposed action *and* a reason, a resume
refused unless it carries an approver *and* a timestamp. When a capability is off, the tool
argument **does not exist** rather than being rejected later.

That is laufwise's own invariant — *"a tool's return value never stands in for truth"*, *"the write
path is sealed by the absence of the method"* (ADR-0004 D7) — applied to the task lifecycle instead
of to domain state. It is what lets you add a planning model without giving up the governance
guarantee: **the model proposes a transition; the platform decides whether it is legal.**

## Where to go next

| You are building… | Read |
|---|---|
| a real-time voice/chat surface | `conversational-agents` |
| an async, approvable, model-planned task | `task-agents` |
| a deterministic graph (what the engine does today) | `workflow-procedures` |
| the record/versioning/publish story for any of them | `agent-contract` |
| the tool seam, allowlists, approval gates | `tools-and-approvals` |
| how you prove any of it works | `evals-and-proof` |
