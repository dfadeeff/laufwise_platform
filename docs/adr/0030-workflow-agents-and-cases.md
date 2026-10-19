# ADR-0030 — Workflow agents and their cases: a workspace creates agents from its modules' workflows

Status: accepted · 19 October 2026 · built (see Implementation status) · step 3 of ADR-0028's
build order · builds on ADR-0006 (tiers), ADR-0022 (kernel and modules), ADR-0029 (files)

## Context

A workspace could create exactly one kind of agent: a voice receptionist. Workflows existed only as
runbook files shipped in code. A workspace could deploy one with "Open workflow", but never
*create* one. A property-management workspace therefore had nothing to create at all, and the
Agents page talked about a third tier (a model planning its own steps) that is not built.

The first real-estate workflow, the Betriebskostenabrechnung, is a different shape from the
calendar import. It is not triggered by a clock over a list of appointments. A person brings the
documents of one piece of work, checks what was read from them, and gets a result computed and
checked by the platform. That piece of work needs a record of its own.

## Success criterion

1. The Studio offers **two** kinds of agent: **conversational** and **workflow**. A workspace
   creates a workflow agent from any workflow its modules offer.
2. A workflow agent's work happens in **cases**: documents attached, an input a person corrects,
   a result computed from it.
3. **A result can only be computed, never written**, and is only shown if the workflow's own check
   accepts it. The kernel, not the module, guarantees both.
4. A module adds a workflow **without writing UI**: the forms and the result view come from the
   module's own dataclasses.

## Decision

### D1 — Two kinds of agent in the Studio, one way to create them

The Agents page is minimal on purpose: a title, the module's one-line intro, **one** primary
action ("New agent"), and the workspace's agents as cards, each showing its kind. Creating starts
with the choice of kind:

- **Conversational**: talks with people in real time (the voice receptionist, where a module
  offers `voice_agents`).
- **Workflow automation**: runs a fixed sequence of steps over a piece of work. It offers every
  workflow the modules declare (D2) and the built-in runbook workflows (calendar import, mirror),
  which open their own setup page.

A kind the workspace's modules do not offer is shown, but cannot be chosen, and says why. A new
workspace is not asked what business it is in: it is offered every module's agents, and the first
one it creates records its module (ADR-0028, amended), labelled by module while the offer spans
several. Call
figures live on the Dashboard; a vertical's onboarding lives inside its own creation form. The
third tier of ADR-0006, the task agent where a model plans its own steps, stays unbuilt and is not
advertised. The layout takes Wonderful's single entry point, with the kind chosen as the first
step, as its inspiration (read 2026-10-19), without its four kinds or its multi-step wizard.

### D2 — A module declares a workflow as four pure functions and its dataclasses

```
Workflow(key, label, summary,
         input_type, settings_type, result_type,       # dataclasses
         parse(input, settings) -> parsed               # raises InputError
         compute(parsed)        -> Computed(value, result, questions, assumptions)
         check(parsed, value)   -> [problems]           # the postcondition
         rules_version, labels, choices, result_omit)
```

`Module.workflows` lists them; the kernel refuses at boot a workflow offered twice, or one whose
types have no form. The module does no I/O and touches no table. The Betriebskosten workflow is
`app/modules/immobilien/betriebskosten/workflow.py`: it adapts the existing `parse_case`,
`settle` and `verify`, and supplies the German labels.

### D3 — The kernel derives the forms from the dataclasses

`app/kernel/schema.py` reads a dataclass and produces a small JSON-Schema dialect the Studio
renders completely: objects, arrays, strings, booleans, integers, decimals (strings with
`format: decimal`, so money never passes through a float), dates and enums. Titles come from the
module's `labels` (`"Type.field"` first, then `"field"`); enums from `choices`, keyed the same
way. One source of truth: change the dataclass and the form changes. A type outside the dialect
is a boot error. This is the generic renderer ADR-0022 D5 asked for, built for its first user.

### D4 — A workflow agent is a name and standing decisions; a case is one piece of work

- `workflow_agent` (tenant, module, workflow, name, `settings`). Settings are the decisions the
  agent applies to every case, rendered from `settings_type`. For Betriebskosten that is the cost
  basis: whether the association's figure for the unit may stand in for the building total.
- `workflow_case` (agent, title, `input`, `input_revision`, `computed_revision`, `rules_version`,
  `result`, `errors`, `questions`, `assumptions`, `problems`, `status`).
- `workflow_case_file` links stored files. **A file a case reads cannot be deleted** (409) until
  it is detached, closing ADR-0029's open consequence.
- `workflow_case_event` is the append-only history: created, input saved, computed (with
  everything it produced), file attached or detached.

### D5 — Inputs are corrected; results are only computed

- Saving the input **clears the result** and needs the revision the editor read (409 otherwise),
  so two people cannot silently overwrite each other.
- Computing runs `parse`, then `compute`, then **the kernel runs `check`** on every result:

| Outcome | Status | Shown |
|---|---|---|
| the input cannot be parsed | `draft` | the errors |
| questions are open | `blocked` | the questions, no figures |
| the check refuses the result | `failed` | the problems, **no result** |
| otherwise | `ready` | the result, with its assumptions |

- Every computation records the input revision and `rules_version`, so a result can be traced to
  the input and the rules that produced it.
- There is no endpoint that writes a result.

### D6 — Computing is not a governed run, yet

A computation writes nothing outside the case: it reads the input and produces a result, so the
governed loop has nothing to protect there. The governed run starts where the platform acts on
the outside world: sending the letters, with the approval gate (step 5). That step will run as a
runbook whose postcondition is this same `check`.

## Not building

- Editing a result, overriding a check, or computing a case whose module the workspace no longer
  runs (403).
- Deleting agents or cases. They are records; archiving comes when someone needs it.
- Versioned agent settings (publish and pin, as conversational agents have). Settings apply at the
  next computation, and each computation records what it used. Versioning comes with approvals.
- Extraction from the documents into the input. That is step 4; today the input is typed or pasted
  as JSON.

## Consequences

- A property-management workspace can create a Betriebskosten agent, attach the documents, enter
  or paste the input, and compute NK-01 and NK-02 in the Studio.
- The next workflow (Kautionsabrechnung, Mieterhöhung) is a dataclass, four functions and labels.
  No page, endpoint or table is added for it.
- The task tier note is gone from the Agents page; ADR-0006 still describes it as unbuilt.

## Implementation status (2026-10-19)

- Kernel: `app/kernel/schema.py` (`object_schema`, `empty`, `plain`), `app/kernel/workflows.py`
  (`Workflow`, `Computed`, `InputError`), `Module.workflows` and boot checks in
  `app/kernel/modules.py`; `app/workflow_agents/service.py`; models `WorkflowAgent`,
  `WorkflowCase`, `WorkflowCaseFile`, `WorkflowCaseEvent`; migration `ad1e2f3a4b5c`;
  `app/api/v1/workflow_agents.py` (`/workflow-agents`, `/cases`); `DELETE /files/{id}` refuses an
  attached file.
- Module: `app/modules/immobilien/betriebskosten/workflow.py`.
- Studio: Agents page with the two categories and "New workflow agent";
  `studio/workflow-agents/[agentId]` (cases, settings); `studio/cases/[caseId]` (documents, input
  as form or JSON, compute, result, history); `features/workflows/SchemaForm.tsx`,
  `ResultView.tsx` (a stacked layout on phones), `CaseStatusChip.tsx`.
- `tests/test_workflow_agents.py`: schema derivation, boot refusals, the full lifecycle of NK-01
  and NK-02 against Postgres, a refused check never shown, stale saves, file protection, isolation
  between workspaces. The renderers were checked in Chrome at 1280 and 375 px with NK-02's real
  schema and result (no horizontal overflow).
