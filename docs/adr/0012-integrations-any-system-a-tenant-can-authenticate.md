# ADR-0012 — Integrations: any system a tenant has credentials for

Status: proposed · 20 September 2026 · extends ADR-0003 (connector), ADR-0004 (governed writes)

## Context

The voice tier had one practice-management system written into it by name: any adapter that was
not `thevea` was refused, however well it implemented the port the agent actually binds to. That
is now a registry (`VOICE_CALENDARS`), which fixes the string comparison but not the shape of the
problem: adding a system still means writing Python in this repo and shipping a release.

A tenant with credentials for a system nobody here has heard of cannot use it, and that is the
wrong answer for a platform whose whole pitch is that the governed loop is the product and the
domain plugs into it.

Three different things get called "an integration", and conflating them is what makes this hard:

| | What it is | Where it lives | Who changes it |
|---|---|---|---|
| **Connection** | credentials + settings for one system, for one tenant | `connection` row, encrypted | the practice, at 3am, without a release |
| **Integration** | the code that turns a connection into callable tools | this repo, or outside it | an engineer, reviewed |
| **Skill** | a prompt plus the tool names it needs | `skills/<name>/` | an author, reviewed |

The first is already right (ADR-0003, and `agent-contract`: credentials never enter the versioned
artifact). The third is right as of today — skills declare `requires` and an agent selects them.
This ADR is about the second.

## Decision

### D1 — Tools come from *sources*, and a source is registered, not named in the runtime

A **tool source** takes a resolved `Connection` and returns `ToolSpec`s. `booking.TOOLS` becomes
one source (the practice-calendar source) rather than the only tool table there is. The voice
surface asks the registry for the tools an agent's selected skills name, and never knows which
source produced them.

This is the same move as `VOICE_CALENDARS`, applied one level up — and it is what makes the rest
of this ADR possible rather than a rewrite.

### D2 — Three levels of integration, and a tenant can reach the second without us

| Level | How a system joins | Who can add one | Guarantees |
|---|---|---|---|
| **L1 — native** | a provider satisfying `PracticeCalendar` (or another port) + a registry entry | an engineer, in a PR | typed port, governed writes, evals, the strongest story |
| **L2 — MCP** | the tenant stores an MCP server URL + credentials as a Connection; its tools are discovered at session start | **the practice itself** | whatever the server offers; read-only by default (D3) |
| **L3 — declarative HTTP** | a manifest describing endpoint, auth and parameters | the practice | rejected for now — see below |

**L2 is the answer to "any system I have credentials for."** Pipecat already ships an MCP client
(`pipecat/services/mcp_service.py`); it needs one dependency (`mcp`) and a Connection type. A
practice that runs an MCP server for its PVS, its CRM, its billing system, gets those tools in
front of the agent without anybody writing Python here. Wonderful lists MCP as a first-class tool
type for the same reason.

**L3 is rejected for now** (CLAUDE.md §III): a manifest that describes an HTTP call is an API
client with no tests, no types and no error handling, re-invented per tenant. MCP already solved
this and has a spec. Revisit only if a customer has a system with no MCP server and no engineer.

### D3 — An unreviewed tool is read-only until a contract says otherwise

This is the line that keeps the platform's promise intact, and it is not negotiable for the same
reason ADR-0002 #11 is not.

- A tool from an **L2 source is offered read-only by default.** The agent may call it to learn
  something; the result is a claim, shown to the caller, recorded in the timeline.
- A tool that **changes anything** must be named in a governed runbook step (`kind: enforced`)
  with a postcondition that re-queries real state. Until a contract names it, the platform does
  not offer it — the argument does not exist, in the `tools-and-approvals` sense.
- Classification comes from the source's own declaration **and** is re-asserted by the engine's
  allowlist. Both, always: a server that lies about a tool being read-only gets one call it
  cannot repeat, not a booking nobody checked.

The consequence is honest and worth saying out loud: **a tenant can plug in any system and have
the agent read from it today; writing through it needs a contract.** That is slower than "install
and go", and it is the entire difference between this platform and a framework.

### D4 — Skills name tools; they never name systems

`skill.json` already carries `tools` and `requires`. A skill says *"I need a tool called
`search_availability` and a connection in the `calendar` role"*. It does not say thevea, and it
does not say MCP. Which source satisfies the role is a per-agent binding, resolved at session
start — so the same reviewed skill, with the same prompt and the same evals, works against a
different practice-management system with no edit.

### D5 — A tenant authoring its own skill is a later decision, deliberately

A skill is a prompt with guardrails. Letting a practice write one means letting them write the
sentence that decides when identity is verified, which the `conversational-agents` skill is
explicit about: prompt-level guardrails are hints, the tool set and the engine are the guarantee.
So tenant-authored skills are safe exactly to the degree D3 holds — read-only tools, contracts
for writes. Build D1–D4 first; revisit authoring once there is a second L2 integration in
production to learn from.

## Consequences

- `booking.TOOLS` stops being the tool table and becomes a source. Nothing about the existing
  tools, prompts, contracts or evals changes — `prompt_sha` must not move on this refactor.
- A new Connection type for MCP servers, with its own credential shape, plus the `mcp` dependency.
- The Studio grows a place to add one: a server URL, credentials, and a list of the tools it
  offered when tested — which is also the review surface, because a practice should see what it
  just gave its agent access to.
- Evals gain a source dimension: a scenario can declare an MCP tool's canned response, so a
  tenant integration is provable the same way the native one is (`evals-and-proof`).

## What this does not change

The governed loop, the append-only write path, the postcondition that decides truth by re-reading
real state, and tenant isolation. An integration is a way to reach a system. It is not a way to
reach them without being checked.
