---
name: evals-and-proof
description: How a non-deterministic agent is proven to work before it ships — eval scenarios per tier, tool mocking, judges, tags and metrics, and the run/task trace as the audit surface. Read before shipping any model-driven behavior, or when asked "how do we know this agent works?".
---

# Evals and proof

laufwise's current proof story is excellent *because everything is deterministic*: a governed run
either satisfied its postconditions or it did not, and the JSONL trace says which. The moment a
model plans the sequence, that story stops being sufficient — a run can satisfy every postcondition
and still have done the wrong work.

Evals are the answer, and they are **not a testing afterthought; they are a first-class, versioned
part of the agent's definition** — `evals/scenarios/` sits next to the config and the instructions,
and moves through the same review.

## The shape of a scenario

Evals test the *shape of the interaction*, not string equality.

| Scenario part | What goes in it |
|---|---|
| **Input** | the work request / opening message the agent starts from |
| **Expected behavior** | the business outcome, in plain language |
| **Tool mocks** | expected tool calls, **input checks**, and optionally mocked outputs |
| **Expected status** | for tasks: the status after a phase, e.g. `action_required` then `completed` |
| **Human reply** | the approval, decline, or refinement used to continue |
| **Judge** | the overall pass/fail criteria |

Two details that make this work in practice:

- **Mocking output and asserting the call are separate switches.** Leave mock output off when the
  real tool should run, while the eval still verifies that the expected tool was called with the
  expected input. So you can assert "it called `create_patient` with this patient" while still
  exercising the real connector.
- **A human reply is part of the scenario.** An approval path you cannot replay in a test is an
  approval path you cannot maintain.

## Evals are tier-specific

You cannot validate a task agent with a chat eval. A `workflow` template is proven by its
postconditions and a completeness report; a `task` agent needs status-and-timeline assertions; a
`conversational` agent needs a transcript judge. **One eval harness, three assertion vocabularies.**

## What to cover — the list that catches real bugs

| Case | laufwise analogue |
|---|---|
| Happy path | an appointment imports and verifies |
| Approval path | the run parks, an approver decides, the run resumes |
| **Tool failures** | the connector raises → `StateUnavailable` → **BLOCK**, never a false "absent" |
| Missing evidence / uncertain identity | the patient match is ambiguous → halt, do not guess |
| Policy exceptions | the working-hours override (ADR-0005 D7) lands in its own bucket |
| Final status | the run's terminal state is the one you expected |

Every one of those is a **fail-closed** assertion — you are testing that the system refuses
correctly, which is the half nobody writes and the half that matters. This is CLAUDE.md §V ("test
behavior that can actually break") pointed at the governance layer.

## Snapshots — what "it passed" refers to

Evals run against a **pinned snapshot**, so a result refers to an exact version of the agent. And:
**a passing eval does not publish anything.** Proof and promotion stay separate.

laufwise's immutable published `(name, version)` is the same idea, and `Run.template_version`
already records which version executed. Keep that, and add the equivalent for instances.

## Tags and metrics — proof at fleet scale

Evals prove one scenario. **Tags** and **metrics** tell you what is happening across thousands of
real runs. They are versioned with the agent and applied at defined moments:

| Tag fires when | Example |
|---|---|
| a skill activates | `billing_inquiry` |
| a tool executes | `invoice_lookup` |
| the skill completes | `payment_explained` |
| something goes wrong | `billing_escalated` |

Terminal outcomes trigger tag + metric evaluation — for `completed` and `failed`. A **declined**
task is deliberately excluded: it is a clean outcome, not a failure to measure.

laufwise has the raw material — `EpisodeEvent` rows and step statuses — and no aggregation layer.
The cheapest useful version: derive tags from step outcomes (`blocked:<step_id>`,
`state_unavailable:<binding>`) and count them per template version. That single chart answers
whether v3 is better than v2, which is a question a platform must be able to answer.

## The trace is the product

For a governed platform the trace is not diagnostics — it *is* the deliverable. The timeline must
record trigger details, agent updates, tool calls, status changes, approvals, refinements,
attachments, and notes. A debugging checklist only works because the timeline is complete.

laufwise's `EpisodeEvent` + JSONL trace is that asset already. Two things to add as the model tier
lands:

1. **A model's reasoning step is an event.** If it is not in the trace, it did not happen — and an
   unexplained model decision is worse than an unexplained deterministic one.
2. **An export.** One artifact containing run + steps + trace + approvals, addressable by id.
   Support conversations are impossible without it.

## Rules

1. Evals are versioned with the agent, not kept in someone's notebook.
2. Assert the shape — tool called, with these inputs, status became this — not the exact words.
3. Mocking output and asserting the call are separate switches.
4. Cover the refusals: tool failure, ambiguity, policy exception, approval decline.
5. Evals run against a pinned snapshot and never publish.
6. A model-driven decision that is not in the trace did not happen.
7. Terminal outcomes drive tags/metrics; a decline is a clean outcome, not a failure.
