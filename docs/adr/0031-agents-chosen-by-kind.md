# ADR-0031 — A workspace chooses a kind of agent, never a domain first

Status: accepted · 20 October 2026 · **amends ADR-0028 D3 and D4** (a new workspace is no longer
asked "What is this workspace for?"; a module is added by creating an agent from it) · builds on
ADR-0030 D1 (two kinds of agent)

## Context

ADR-0028 D3 had a new workspace choose its module before anything else: "Medical practice" or
"Property management". In the Studio this read as a domain picker on the Agents page, and the
rail, the Agents intro and the welcome card then spoke for that domain ("Meet your new
receptionist"). The owner's verdict on seeing it: the platform must not divide by domain but by
**type of agent**. A customer comes to create a conversational agent or a workflow agent; which
vertical's code serves it is the platform's business.

## Decision

### D1 — The Studio offers kinds, and then what each kind can be

A workspace with nothing in it is offered two cards: **Conversational agent** and **Workflow
agent**. A conversational agent is set up as before. A workflow agent is created from a list of
**every** installed workflow (Betriebskostenabrechnung today); the domain appears only there, as
the workflow's own name. The rail and the Agents intro use one neutral wording for every
workspace; no module supplies Studio copy any more.

### D2 — Creating an agent adds its module; the server still holds the workspace to it

`workspace_module` stays: it is the record of what a workspace runs, and the gates of ADR-0028
D4 and red flag C9 still read it. It is now written by creation, not by a choice up front:

- `POST /agents` (conversational) adds the module that provides `voice_agents`
  (`kernel.modules.providing`, the first in `INSTALLED` order);
- `POST /workflow-agents` adds the module the chosen workflow belongs to.

So a workspace that never created a conversational agent still cannot claim a phone number or
start a voice session (403), and one that never chose a practice workflow cannot deploy a
practice runbook. `POST /workspace/modules` remains for operators and tests; the Studio no longer
calls it.

## Consequences

- The domain picker, the receptionist welcome card and per-module Studio copy are gone;
  `Module.pitch` and `Module.intro` are no longer read by the Studio.
- The conversational setup form still asks for a practice name and practice type, because the
  only conversational kit today is the practice receptionist (red flag A3). It becomes generic
  when the conversation kit is extracted (ADR-0022 step 2c).
- When a second module provides `voice_agents`, "the first in `INSTALLED` order" is no longer
  enough: the conversational setup will list what each provides, as the workflow list does.
