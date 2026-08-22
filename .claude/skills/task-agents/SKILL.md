---
name: task-agents
description: How to build async, model-planned, human-approvable task agents in laufwise — the task state machine, triggers, protocol tools, the approval pause, the timeline as audit trail, and containment. Read before adding any agent that works on a task rather than in a conversation, or before touching task status, triggers, or approvals.
---

# Task agents

A task agent is the tier laufwise does not have yet and most needs. It is what turns a governed
*procedure* engine into an *agentic platform*: a model that plans a sequence of tool calls against
a durable, reviewable task, pausing for a human when the stakes demand it.

> **Use one when the work is operational, reviewable, and not bound to a real-time conversation.**
> Document review · email operations · approval workflows · scheduled audits · data enrichment.
> Wrong for anything a customer is waiting on live — that is a conversational agent.

## The shape

Five parts, all of them persistent:

```
trigger ──► task (type, context, status) ──► agent loop ──► task events (timeline)
                        ▲                        │
                        └──── human decision ◄───┘  (approve / decline / refine)
```

| Part | What it is |
|---|---|
| **Trigger** | how a task is created: manual, schedule, webhook, email, inbound message, or *from a procedure step* |
| **Task** | `instance_id`, `task_type`, `trigger_type`, `status`, `context` (jsonb), plus the approval fields |
| **Agent loop** | model plans → calls tools → the platform records every call |
| **Task events** | the append-only timeline: trigger details, updates, tool calls, status changes, approvals, refinements, attachments, notes |
| **Decision** | approve · decline · **refine** (feedback, agent continues) |

The timeline **is** the audit trail, not diagnostics beside it. laufwise already believes this —
it is `EpisodeEvent` — but today events only ever come from a deterministic engine. Task events
come from a model, which is exactly why they matter more.

## The state machine

```
pending ──► live ──► completed
   │         ├──► failed
   │         ├──► cancelled
   │         ├──► cancelling ──► cancelled
   │         └──► action_required ──► live       (approved / refined)
   │                              └──► cancelled (declined)
   └──► failed | cancelled

terminal (completed | failed | cancelled) ──► live   (a new message resumes the task)
```

Three properties are worth more than the diagram:

1. **Transitions are validated, not trusted.** A pure `can_transition_to(next)` predicate; a task
   cannot jump `pending → action_required`. Unit-test exactly that, the way
   `control_plane/runner.py` is testable without a database.
2. **A pause must carry its justification, enforced at the transition.** Refuse
   `action_required` unless *both* a `proposed_action` and an `action_required_reason` are
   non-empty; refuse the resume unless an approver **and** a timestamp are set; refuse the
   decline-cancel unless a decliner and timestamp are set. You cannot end up with an approval
   nobody signed.
3. **Rejecting is not failing.** Decline → `cancelled`, a clean terminal state with an author.

### How the model touches it — the protocol-tool seam

The agent **cannot** set status by returning a value. It calls a protocol tool the platform owns —
`task_set_status` — always present in context, never parallel-safe, and whose enum is **narrowed at
definition time**: `action_required` only appears in the schema when that agent is allowed to pause.
When a capability is off, the tool argument does not exist rather than being rejected later.

> This is laufwise's own doctrine — *"the postcondition decides by re-querying real state"*, *"the
> write path is sealed by the absence of the method"* (ADR-0004 D7) — applied to the lifecycle.
> Build the task tier this way or the governance story has a hole exactly where the model is.

## What to build, and where

Nothing here belongs in the engine. Every piece is a seam (§XII).

| Piece | Where it goes | Notes |
|---|---|---|
| `Task` + `TaskEvent` models | `app/db/models.py` | tenant-scoped like everything else; `context` jsonb; append-only events (mirror `EpisodeEvent`) |
| Status enum + transition guard | a pure module, e.g. `app/tasks/state.py` | **no DB, no FastAPI** — unit-testable in isolation |
| Triggers | `app/db/models.py` + `app/api/v1/triggers.py` | operational records, *not* template YAML — see below |
| The agent loop | `app/workloads/task/` behind `ExecutionAdapter` (`app/workloads/base.py`) | the only place a model acts |
| Protocol tools | `app/workloads/task/protocol.py`, registered via the tool registry | `task_set_status`, `task_request_approval`, `task_note` |
| Approval decisions | extend `app/api/v1/approvals.py` + the `Approval` model | it already has `decided_by`/`decided_at` — align with approve/decline semantics |
| Queries | `app/db/repo.py` only | the one place queries live |

### Triggers are records, not files — and this is deliberate

An agent's behaviour is versioned and reviewable; **where its work comes from is an operations
concern that changes without a release.** Ops must be able to disable an entrypoint at 3am without
a code review. Same reason laufwise keeps `Connection` rows out of the template contract and binds
them per-instance.

Trigger fields worth having: `trigger_type`, `task_type` (required — pick meaningful values like
`refund_review`, `daily_reconciliation`; they become the filter/eval/debug axis), `is_enabled`,
`task_context` (defaults merged into every run), and for schedules exactly one of a structured
`schedule_config` (`every`/`unit`/`at_time`/`days_of_week`/`day_of_month`) **or** a cron
expression, plus an optional IANA `timezone`. Runtime fields (`webhook_secret`, `public_url`,
`next_run_at`) are server-owned — never accepted on input.

laufwise already has proto-tasks: `ImportJob` and `app/connections/doctolib_login_jobs.py`. When the
Task tier lands, `ImportJob` should become a `Task` with `task_type: calendar_import` — that
migration is what proves the tier is right.

## Task instructions read like an SOP

Not a personality:

```text
You review incoming refund requests.

For each task:
1. Read the request and attached evidence.
2. Look up the customer's order and refund history.
3. Compare the request against the refund policy.
4. If the refund changes account balance or exceeds the allowed amount, pause for approval
   with a short reason and proposed action.
5. After approval, update the system and summarize what changed.
6. If information is missing, ask for a refinement instead of guessing.
```

Lines 4 and 6 carry the weight: **the approval boundary and the do-not-guess rule are in the
instructions, and enforced by the platform underneath.** Prompt-level policy is a hint; the state
machine and the tool gate are the guarantee. Never rely on only one of the two.

## Containment

A model planning arbitrary work needs a **blast radius bounded by construction**, not by the
allowlist happening to be correct. Mature platforms run each task in a per-tenant container with an
init command, secret-referenced env vars (never inline credential values), and an optional cache
persisted between runs.

laufwise does not need containers on day one — its tool registry is narrow and Python-side. But be
explicit about what buys the containment, and do not let "the allowlist is correct" be the only
answer.

## Skill packages — instructions that carry their materials

A task agent's skill should be a *directory*, not a prompt string: an instruction file (when to use
it, the workflow) plus the reference material — policies, mapping tables, checklists, helper
scripts, sample outputs. The agent pulls in what it needs *during* the task, which keeps complex
work accurate without turning the main instructions into a manual.

Directly applicable here: a `calendar_import` task agent's patient-matching rules, room-mapping
table, and the working-hours override policy (ADR-0005 D7) belong in a skill package — not in a
prompt, and not hard-coded in `app/workloads/import_tools.py`.

## Debugging checklist

| Question | Where to look |
|---|---|
| Did the task start? | task list, trigger record, status, task id |
| Did trigger data arrive? | task `context`, webhook payload, attachments |
| Is the agent stuck? | timeline events, current status, recent tool calls |
| Why does it need review? | the pause record: reason, proposed action, prior tool results |
| Did a tool fail? | tool-call and tool-result events in the timeline |

Every row is answerable **only if the timeline is complete**. Partial event writing is the bug that
makes a task agent unsupportable.

## Rules

1. Status changes go through the guard. No `task.status = ...` outside the transition function.
2. A pause without a proposed action + reason is invalid — enforce at the transition, not in a form.
3. A resume or decline without an author and a timestamp is invalid.
4. Tool result ≠ truth. A task that claims completion still gets verified wherever a verifiable
   postcondition exists (§0 fail-closed; ADR-0004).
5. Capability off ⇒ the tool argument is absent from the schema, not rejected at runtime.
6. Triggers are operational rows; task behaviour is versioned. Never merge them.
7. Every state change writes a task event, in order, before the response returns.
