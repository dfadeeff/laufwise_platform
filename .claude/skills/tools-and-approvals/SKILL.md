---
name: tools-and-approvals
description: The tool seam and the three independent control gates — per-call tool approval, plan/task approval, and automated policy. Covers where tools live (agent vs skill vs shared), tool notes, error handling, and why a model must never be trusted to gate itself. Read before adding a tool, an approval, or any guardrail.
---

# Tools and approvals

> Without tools, an agent can only talk. A tool is the **only** place an agent affects the world.

laufwise already states this (`app/workloads/base.py`: *"the ExecutionAdapter seam — the only place
a model acts"*). This skill is about the two things around it: **where a tool lives** and **what
stands between the model deciding to call it and the call happening**.

## Tool types

| Type | What it is | Use for |
|---|---|---|
| **Code** | a callable in the repo that hits an HTTP endpoint with logic around it | most tools |
| **Knowledge** | queries a knowledge base / RAG | policy questions, FAQs |
| **MCP** | an external MCP server | third-party integrations |
| **Integration** | an action on a connected system | pre-built actions |

**There should be no "mock" tool type.** To give a tool a canned response in a test, mock it in an
*eval scenario* — that leaves the tool's own code untouched. A test-only branch inside production
code is a bug waiting to ship. laufwise's equivalent risk is `ExecuteDef.effect` — demo-only,
ignored by real connectors, with postconditions re-querying real state regardless. Keep that
boundary that sharp; better still, move simulation entirely into the fixture provider and delete
the field when the demo path retires.

## Availability vs ownership — two separate questions

**Availability** — *when can the model call it?*

| | When available | Example |
|---|---|---|
| **Skill tools** | only while that skill is active | `cancel_order`, `process_refund` |
| **Agent tools** | always | `create_ticket`, `send_sms`, `escalate_to_human` |

**Ownership** — *who may reuse the implementation?*

| Location | Use when |
|---|---|
| Shared library | several agents reuse the same implementation |
| Agent-private | one agent, available throughout |
| Skill-private | one skill, available only while it's active |

Two pieces of guidance worth memorising:

- **Keep agent-level tools minimal.** Every one sits in context for the whole conversation.
- **Use skill tools for sensitive actions.** A `delete_account` tool should not be reachable at all
  times — scope it to a skill with guardrails. **Scoping availability is itself a control.**
  laufwise says the same thing with the per-step `tools: [...]` allowlist, at finer grain.

## Writing a good tool

| Principle | Detail |
|---|---|
| Clear name + description | the model selects on these. `lookup_order_status` ≫ `order_tool` |
| Minimal parameters | if a phone number is enough, don't also require an email |
| Useful responses | what the agent needs to answer — not a database dump |
| Graceful failures | a message the agent can act on |

**Handled errors** return something usable:
```json
{ "error_message": "No order found with ID 12345. Ask the customer to verify the order number." }
```
**Unhandled errors** (crashes, timeouts) return a generic failure — internal details never reach
the model or the customer.

### Tool notes — data plus guidance

```json
{ "status": "delayed", "new_eta": "Monday",
  "agent_notes": ["Apologize for the delay", "Offer 10% discount code SORRY10"] }
```

`agent_notes` shape what the agent says next, and can be generated dynamically by the backend (a
VIP gets different treatment than a first-time buyer). **This is how you move policy out of the
prompt and into the system that actually knows the policy** — the prompt goes stale, the backend
does not.

## The three gates — independent, and they compose

Get this right. There are **three** separate mechanisms, and they are not substitutes:

| Gate | What it decides | When to use |
|---|---|---|
| **Tool approval** | a person approves *each call* of one specific tool, with **editable arguments** | one tool is high-stakes: refunds, account changes, outbound money |
| **Plan / task approval** | the whole task pauses; a person reviews the agent's *direction* | the plan needs review, not one call |
| **Automated policy** | machine checks on task inputs, replies, and extracted attachment text | boundaries that must not depend on a human being available |

They compose: a task agent can pause its plan for review **and** still stop at each gated tool call.

### Tool approval, in detail

Mark a tool `requires_approval` at registration. Every call then pauses **before execution** and
shows the proposed arguments as an **editable form**.

- **Approve** → runs once with exactly the values on the form.
- **Approve with edits** → same, corrected. The agent is told (`args_edited: true`, computed by the
  platform) so it can adjust the rest of its plan.
- **Deny** → never runs; the agent is told and works out what to do next.

The properties that make it a real control rather than a prompt:

1. **The gate is in the platform, not in the prompt.** No instruction, however creative, lets the
   agent run the tool without that decision.
2. **What you confirm is what runs.** Approved values are validated against the tool's schema and
   passed verbatim. The model gets no chance to change them between decision and execution.
3. **Every call asks.** There is no "always allow".
4. **Invalid edits are refused immediately** and the request stays open.
5. **A new message supersedes an open request** — the agent re-proposes if still warranted.
6. **Waiting requests survive restarts.**
7. The task **status stays `live`** while parked on a tool approval; a separate computed field
   marks it as waiting. Automate against that field, not the status.

And the operational rule people miss:

> If a tool's prompt says "ask the operator before running X", **remove that choreography when you
> turn on `requires_approval`** — otherwise the reviewer answers twice for one action. Tell the
> agent which tools pause and that **calling the tool *is* the request**, so a gated tool is called
> directly with the agent's best proposal.

### The warning to internalise

Tool approval only works on surfaces where a human is there to decide: **task work and
operator-assist sessions**. A customer-facing call cannot wait on a reviewer, so a real-time surface
must never rely on this gate.

**A gate that cannot block is not a gate.** That is exactly why laufwise compiles a conversational
agent's consequential actions into governed steps, or hands them to a task (see
`conversational-agents`).

## How this lands in laufwise

The engine already enforces the strongest version of all three, but only for *authored* steps:

| Gate | laufwise today | Gap |
|---|---|---|
| per-call tool approval | `StepDef.tools` allowlist + `ApprovalDef.required_when` | approval is per-**step**, not per-**call**; arguments are not editable |
| plan/task approval | — | needs the task tier (`task-agents`) |
| automated policy | preconditions + `StateUnavailable` → BLOCK | per-runbook only; no cross-cutting policy layer |
| `args_edited` feedback | — | when an approver edits args, the executor must be told |

Concrete additions, all at existing seams:

1. **Per-call approval on the tool registry**, not only per step. A tool flagged
   `requires_approval` parks the run wherever it is called from.
2. **Editable arguments** on the approval record, validated against the tool's declared schema
   before execution. `Approval` already has `note`/`decided_by`; add `proposed_args`/`final_args`.
3. **Report `args_edited`** back to the executor.
4. Keep the **allowlist re-assertion**. `app/workloads/base.py` already demands the adapter refuse
   out-of-allowlist calls *and* the engine re-assert it — defense in depth. Never drop one because
   the other exists.
5. `requires_approval` is a **capability of the tool registration**, so a tool without it cannot be
   gated by a prompt and a tool with it cannot be un-gated by one.

## Rules

1. A tool result is a claim, never truth. Verify by re-querying state.
2. Gates live in the platform. A prompt-level "ask first" is documentation, not a control.
3. Approve-with-edits runs exactly the confirmed values, and the model is told they were edited.
4. Per-invocation, always. No "always allow".
5. A gate on a surface that cannot wait is theatre — restructure the work instead.
6. Availability is a control: scope dangerous tools to the narrowest step/skill that needs them.
7. No test-only branches in tool code. Mock at the scenario layer.
