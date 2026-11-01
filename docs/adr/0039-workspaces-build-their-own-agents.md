# ADR-0039 — A workspace builds its own agents: building blocks as data, guardrails the platform enforces, functions in a sandbox

Status: proposed · 1 November 2026 · not built · builds on ADR-0002 (Studio), ADR-0028 (modules),
ADR-0030 (workflow agents and cases), ADR-0031 (agents by kind), ADR-0033–0035 (reading, letters,
approvals), ADR-0038 (rules packs); replaces where ADR-0030's workflows live and who writes them

## Context

The platform should let a small business set up its own agents, safely, without us writing code
for each one. Today it does the opposite.

**Two workflow systems that do not meet:**

1. **Runbooks** (`backend/runbooks/*.yaml`, `runner.py`): a data-defined step graph with the
   governed loop (precondition → allowlist → approval → execute → postcondition → trace). Voice
   and the calendar import run on it. No branch, no wait, no durable resume.
2. **Case workflows** (ADR-0030, `app/modules/*/<workflow>/`): forms from schemas, reading
   documents, the letter check, approvals. All generic. But each workflow is Python inside the
   platform, with one fixed shape (parse → compute → check → letters), written by us.

**What that costs.** Every use case is platform code and a deploy. The fixed shape does not fit
NK-03 (two procedures, one decision about their letters) or NK-04 (deadline analysis, a person's
decision, consequences). And generic capabilities get built inside one workflow: NK-02's "one fact
is missing: from whom, by when; the rest stands" (commit 79b564a) is a wait step every procedure
needs. It now lives in `betriebskosten`.

**What Wonderful shows** (source read in `work_projects/wonderful/repos/`):

- **Users build agents from building blocks that are data:** agent, skill, tool, procedure,
  function, table, tests, all versioned, with draft → review → publish.
- **A wizard and a chat builder.** A wizard creates an empty agent. A chat builder edits the same
  draft: prompts, tools, even tests. The user reviews the change and publishes or discards it.
- **A procedure is a graph with one trigger.** Its steps are function, branch, table, wait (time,
  condition, callback), approval (rejection is a path), agent, sub-procedure, loop. Runs persist
  after every step, resume after a restart, and expire when unattended.
- **Functions are small TypeScript run in a sandbox** (WebAssembly, no host APIs). That is what
  makes code written by users and builders safe.
- **Guardrails are switches and policies the platform enforces:** per-tool approval with editable
  arguments; action policies combined by precedence (block > escalate > approval > allow) with
  observe and enforce modes; rules files validated, then active, then superseded; a status tool
  limited to an enum, with a checklist before "completed"; roles for edit, publish and approve.

**What Wonderful does not do, and we must.** Its campaign demo has deterministic checks, but the
procedure moves on when the model *writes* "APPROVAL_ELIGIBLE: YES". The gate trusts model text.
Tests before publish are optional. Our rule is the opposite and stays our edge: a check always
runs and decides from real state, and nothing a model writes opens a gate.

## Success criterion

1. **A workspace administrator builds NK-03 as an agent in the Studio**, without a developer and
   without a change to platform code. They start from the Betriebskosten template or from the
   documents through the builder, review the draft, the tests pass, and they publish.
2. **Every guardrail the Studio shows is enforced by the platform.** None depends on a prompt.
   Removing an instruction from a prompt removes no guardrail.
3. **The platform contains no domain code.** Delete every template, and the platform builds and
   its own tests pass (CLAUDE.md § XII).
4. NK-01 and NK-02 give the expert's figures after the move (`test_betriebskosten_gold.py`).

## Decision

### D1 — Two layers

| | Platform | Agent definition |
|---|---|---|
| **Is** | code we write and deploy | data a workspace creates, versioned |
| **Owns** | step kinds, engine, runs, forms, reading documents, letter check, approvals, mail, sandbox, tests runner, Studio, builder | one agent's procedure, functions, forms, rules, letter wording, tests |
| **Knows** | what a step, a form, a check and an approval are | what a tenancy and § 9b are |

A module (ADR-0028) stays the grouping a workspace chooses (property management, medical practice).
It now brings templates, not code.

### D2 — An agent is a versioned definition built from blocks

What a workspace creates, each block data:

| Block | What it is |
|---|---|
| **Agent** | the thing a workspace runs, of a kind (ADR-0031): workflow or conversational |
| **Procedure** | the steps, as a graph with one trigger (D3) |
| **Form** | input, settings and result, as JSON Schema with German labels |
| **Function** | deterministic code, run in the sandbox (D5) |
| **Rules** | a rules pack (ADR-0038): validated, then active, then superseded |
| **Letter** | the wording of what is sent, filled from a ready result |
| **Tests** | cases with expected results and where each one came from (ADR-0038 D4) |

The definition is stored in the database as immutable versions. You edit a draft, publish a
version, and a case keeps the version it was computed with. That is the template lifecycle that
exists (ADR-0002, the publish gate), applied to every block. A definition can be exported as files
and imported again, so a template can live in the repository and be reviewed like code.

**Templates.** Betriebskostenabrechnung and Kautionsabrechnung become templates: published
definitions a workspace copies and adapts. A small business starts from a template, not from an
empty agent.

### D3 — The step kinds the platform owns

| Step | Does |
|---|---|
| **input** | the trigger: a form a person fills, or documents read into it |
| **read** | a model fills the form from documents; a person confirms (ADR-0033) |
| **function** | runs a function in the sandbox; returns a result, questions, assumptions, open items |
| **check** | a function that verifies the step before; runs always, cannot be removed |
| **branch** | on a value of an earlier step's structured output, never on text a model wrote |
| **wait** | until a named fact arrives from a named party by a date, or a date passes |
| **decide** | a person's judgment with a reason (ADR-0036 D2; NK-04's responsibility for a late settlement) |
| **letters** | writes texts from a ready result; every amount must be in the result (ADR-0034) |
| **approval** | a second person approves exactly what is sent (ADR-0035); rejection is a path |
| **send** | mails it |
| **agent / llm** | AI as one step, where a definition wants it; its output is structured and checked |

Data flows by reference to an earlier step's output (`{{read-1.positions}}`), with each step kind
owning its output contract, as Wonderful does. A run persists after every step, resumes by id
after a restart, and an unattended wait or decision expires (`workflow-procedures` skill). The
governed loop of enforced runbook steps stays closed for modification; these kinds sit beside it.
A case becomes a run of its agent's procedure.

### D4 — Guardrails: a panel of plain questions, enforced by the platform

The Studio shows every agent's guardrails as questions with their answers, on by default:

| Question in the Studio | Enforced by | Can be switched off |
|---|---|---|
| Is every function's result checked? | a check step after every function | **no** |
| Can a letter say anything the result does not? | the letter check | **no** |
| Who approves before anything leaves the workspace? | the approval step; requester ≠ approver | the approver is chosen; the step is **not** removable for anything sent to a third party |
| Which tests must pass before publishing? | the publish gate runs every test, including the counter-tests | **no**; a person chooses which cases |
| Which rules version is active? | rules lifecycle: validated, active, superseded | a person activates |
| Who may edit, publish, approve? | roles, from the signed token | assigned per person |
| What may the agent touch outside? | the allowlist of the governed loop; per-action approval | per action |

"Can be switched off: no" is the point. A guardrail a builder or a hurried admin can turn off is
a default, not a guardrail. Later, policies with observe and enforce modes (Wonderful's precedence
model) cover actions that need a judgment, not a rule.

### D5 — Functions run in a sandbox

A function is code, because the domain needs code: § 9b splits, largest-remainder rounding,
deadline arithmetic. Users do not write it by hand. The builder writes it, and the user reviews
what it does in plain language and through its tests. So it is generated code, and it runs where
it cannot harm anything:

- **Pure.** JSON in, JSON out. No network, no files, no clock: today's date, the active rules and
  every fact come in as input. Same input, same output, so a result is reproducible from its
  input and its versions.
- **Bounded.** Limits on time, memory and output size. A function that exceeds one fails its step;
  the run does not hang.
- **Python.** The calculators are Python today, the builder writes it well, and `decimal` and
  `fractions` give exact money arithmetic. A function may import the standard library and the
  platform's helper library (money, dates, splitting in whole cents, German formatting). That
  library is platform code and holds no domain rules.
- **Isolated by WebAssembly.** CPython compiled to WASI, run under wasmtime: no host access unless
  granted, and we grant none. This is decided on a spike (step 1). If WASI Python cannot run the
  NK-02 calculator within the limits, the fallback is a separate process under an OS sandbox
  (nsjail) with no network and a read-only file system, and this ADR is amended.

### D6 — The builder writes drafts, never publishes

A model drafts a whole definition, from a description or from an expert's documents (a task, a
solution sheet, review notes), as ADR-0038 D3 describes. That is what was done by hand for NK-01
and NK-02. The builder is itself governed:

- It writes drafts only. Publishing is a person's act, behind the publish gate (D4).
- It cannot switch off a guardrail. A change to the guardrail panel is shown as such.
- It may not write an expected result from its own output. An expected figure it proposes is
  marked unverified until a person confirms it against the named source (ADR-0038 D4).
- The user reviews a summary in plain language next to the change: what the agent does, which
  rules it applies, which tests it passes.

### D7 — The order a workspace meets this

1. Choose a kind of agent (ADR-0031), then a template, or describe the work, or upload documents.
2. The builder prepares a draft. The user reads what it does, corrects it by talking to the
   builder or in the step forms.
3. Run the tests. Run a real case as a test, labelled as real (`workflow-procedures` skill: a test
   run is a real run).
4. Publish. The guardrail panel shows what holds.

## Not building

- A visual canvas. The step forms of ADR-0002 and the builder come first; a canvas when
  procedures outgrow a list.
- Several environments, promotion between them, and traffic routing.
- Tables as a block, PII masking, conversation policies, judged by a model. Each comes with the
  first agent that needs it.
- Functions in another language than Python.

## Consequences

- The kernel's `Workflow` dataclass and `app/modules/*/` code go. Their four functions become
  sandboxed functions of the two templates. Forms become JSON Schema authored as data, not derived
  from dataclasses. ADR-0030 D2 and D3 are replaced by this.
- ADR-0038 stands, amended to match: its rules pack is a block of the definition, written by the
  builder into a draft and published by a person.
- CLAUDE.md § 0 and § XII describe `app/modules/` as where domain logic plugs in. They are
  updated when step 2 lands, so the document does not describe code that is not there.
- The engine gains branch, wait, decide and durable resume. Voice and calendar runbooks get them
  too.

## Implementation steps (when accepted)

Each step is one pull request and keeps NK-01 and NK-02 green.

1. **The sandbox.** Spike WASI Python under wasmtime; then a function runner with the limits of
   D5 and the helper library. Proof: the NK-01 and NK-02 calculators run in it and the gold tests
   pass through it.
2. **Definitions as data.** The blocks of D2, versioned, with export and import. Betriebskosten
   and Kaution become templates; `app/modules/` code goes. Proof: the deletion test.
3. **Durable runs.** Run lifecycle, branch, wait, decide, approval and letters as steps; a case is
   a run. NK-02's open item becomes a wait step.
4. **The publish gate runs the tests**, including the counter-tests, and the guardrail panel's
   fixed answers.
5. **The Studio.** Create from a template, edit the draft in step forms, see the guardrail panel,
   run tests, publish.
6. **The builder.** Draft a definition from a description or from documents.
7. **Acceptance.** NK-03 and NK-04 built through the Studio and the builder, with no platform
   change. The runs' answers go to the expert as unverified snapshots (ADR-0038 D4).
