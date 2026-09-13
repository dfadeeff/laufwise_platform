---
name: conversational-agents
description: How to build real-time voice/chat agents in laufwise — the base prompt vs skill prompts, multi-skill routing, channels, turn-taking, and the rule that a conversational surface must not be its own governance authority. Read before touching `app/workloads/conversational/`, adding a voice/chat channel, or writing an agent's instructions.
---

# Conversational agents

A conversational agent talks to a person in real time. Its clock is sub-second, its loop is
human-turn ↔ model, and it can never wait for a reviewer — which is the source of every rule
below.

laufwise currently has **only a placeholder** (`backend/app/workloads/conversational/__init__.py`).
The docstring there already has the right instinct: *"its tool calls route through the runtime as
runbook steps rather than executing directly."* Keep that. It is what makes a laufwise
conversational agent different from everyone else's.

## The anatomy

| Component | What it does | Lives in |
|---|---|---|
| **Instructions** (`base.md`) | identity, tone, boundaries, escalation | one markdown file, versioned |
| **Skills** | focused capabilities, each with its own prompt + tools + tags | `skills/<name>/` |
| **Tools** | the only way to affect the world | see `tools-and-approvals` |
| **Channels** | voice, chat, email, widget — each with its own greeting/timeouts | config, per channel |
| **Voice pipeline** | STT → LLM → TTS, turn-taking, interrupts | runtime, not authored per agent |

## base.md — the shape that works

Use this section skeleton. Every heading is load-bearing:

```
# Role & Objective          ← who you are + what "successful interaction" means, concretely
# Personality & Tone        ← Personality / Tone / Length / Language / Variety
# Context                   ← {{day}}, {{hour}}, channel, what systems it can see
# Tools                     ← "Use only the tools provided by the active skill.
                               Do not mention or invent tools that are not available.
                               Do not narrate tool or skill activation; activate and proceed."
# Instructions / Rules      ← Scope (in/out, and where out-of-scope routes) + hard Rules
# Conversation Flow         ← named states: "State 1: Answer the request", "State 2: Follow-ups"
# Safety & Escalation       ← what it must never do; how it hands off
```

The lines that repeatedly matter:

- *"Never answer from memory what a tool can answer; always get it from the skill's tool."* — the
  anti-fabrication rule, written into the prompt **and** enforced by the runtime. Both, always.
  ADR-0003 D4 says the same thing at the provider layer.
- *"Do not repeat the same sentence twice; vary phrasing so it does not sound robotic."*
- *"This agent is read only; it never changes data."* — a capability stated in the prompt *and*
  guaranteed by the tool set. If the prompt is the only thing stopping a write, it is not stopped.

`{{agent_name}}`, `{{day}}`, `{{hour}}` are runtime-substituted. Keep the set tiny and declared.

## Skills — the unit you can reason about and swap

It is tempting to pour everything into one big prompt: billing, support, verification, refunds, all
crammed into a single brain. It works for a demo, then quietly turns into something nobody wants to
touch.

A skill = name · display name · description · prompt · tools · tags. **A skill belongs to exactly
one agent.** Reuse happens through a shared library of *tools*, never through a shared skill —
because two agents that appear to need "the same billing skill" almost always need different
guardrails, and sharing the skill silently shares the guardrails too.

Design rules that survive contact with production:

| Rule | Why |
|---|---|
| Name skills for **outcomes**, not mechanics — `Update Address`, not `CRM Write` | product and ops must be able to read the list |
| Start from **journeys**, not org charts | survives reorgs |
| One sentence must describe it: *"handles X for Y kind of request"* | if it doesn't, split |
| Prompt, tools, and tags must describe the **same domain** | drift here is where agents start lying |
| Separate **read-only** flows from **state-changing** flows | lets you attach stricter approval/eval to the risky half |
| Prompt longer than a page ⇒ too big | split it |

A good skill prompt has five parts: **Purpose · Scope · Constraints · Behavior · Success**.
`Constraints` is the one people skip and the one that makes routing work — *"Do not process
refunds — transfer to the Disputes skill."*

### Multi-skill mode

Default: all skills loaded from the start. **Multi-skill mode:** exactly one skill active; the
model calls `switch_skill` and the system *rebuilds the prompt* around the new skill. Its tools
become callable and the previous skill's stop being callable.

Use it when the agent spans genuinely different domains and you want tool availability to follow
the topic. Do not use it for three related skills — you buy confusion, not focus.

## Voice behaviours you do not author per-agent

| Behaviour | What happens |
|---|---|
| Turn-taking | listens for pauses; sensitivity tuned for fast/slow speakers |
| Interruptions | agent stops and listens, automatically |
| Announcements | *"One moment while I look that up"* — spoken but **not** added to conversation memory |
| Non-interruptible | confirmation numbers, critical read-backs, cannot be cut off |

`announcement` is the trick worth remembering: it hides tool latency without polluting context.

## The laufwise-specific rule

**A conversational surface must not be its own governance authority.**

Real-time is precisely where governance is weakest: nobody can wait for a reviewer, so a
human-in-the-loop approval gate simply cannot fire on a live call. **A gate that cannot block is
theatre.**

laufwise's answer is already sketched in the placeholder docstring: a conversational agent's
consequential tool calls **compile to governed runbook steps**. The dialogue turn is a `kind: trace`
step (never reaches the engine); the action is a `kind: enforced` step that goes through
precondition → allowlist → approval → execute → postcondition. `praxis_appointment.yaml` already
models this shape.

Concretely:

1. Chat is a surface, not an authority. It proposes; the engine decides.
2. A tool the model can call in real time is either **reversible/read-only**, or it is an
   *enforced* step whose postcondition re-queries real state.
3. Anything that cannot pass a postcondition in real time becomes a **task** (see `task-agents`) —
   the agent says "I've raised that for review" and hands off. That is the composition seam, and it
   is the honest answer to "the customer is waiting."

## Channels

One agent serves many channels. Per-channel: greeting/initial message, timeouts (conversation time
limit, user-inactivity check-in → disconnect warning → end), idle messages, and channel-specific
tool availability (a call-transfer tool only on voice; an email-send tool only on email).
**Core behaviour stays shared** — do not fork an agent per channel.

## Rules

1. Instructions are a versioned file, not a database string.
2. A skill belongs to one agent. Share tools, never skills.
3. Prompt-level guardrails are hints; the tool set and the engine are the guarantee. Write both.
4. Never let the model answer from memory what a tool can answer.
5. Consequential actions compile to enforced steps or become tasks. No third option.
6. Do not narrate tool/skill activation to the user.
