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
  granted, and we grant none. Decided on a spike (`backend/spikes/function_sandbox/`), below. The
  fallback, had it failed, was a separate process under an OS sandbox (nsjail).

**Spike result (1 November 2026).** CPython 3.14.7 for WASI (brettcannon/cpython-wasi-build),
wasmtime 49 (PyPI), on a laptop:

| Question | Result |
|---|---|
| Same result as in the platform? | NK-01 and NK-02 settlements identical to the in-process run, field for field; the expert's figures (171,40 €, 58,47 €) come out |
| Deterministic? | two runs, identical output |
| Cost per call | about 0,5 s, of which about 0,35 s is the interpreter starting; 4,4 billion fuel units. Compiling `python.wasm` 0,7 s once, then cached |
| Host files | only the two read-only mounts are visible; reading `/etc/passwd` fails, every write fails |
| Network, processes | no socket connect in the WASI build; starting a process fails |
| Endless loop | stopped by the fuel limit (50 billion, about 3,9 s), and independently by the 10 s timer |
| Memory | a 256 MB limit raises MemoryError inside the function |
| Output flood | 20 MB refused at the 4 MB cap |
| Clock | readable, as WASI grants it. Functions get dates as input, and the determinism check holds that |

For step 2 it leaves four things to do: mount only `python.wasm` and `lib/`, nothing else beside
them; pin `python.wasm` by checksum in the image; report which limit stopped a function from the
trap code, not its message; and, only if 0,5 s per call matters, start from a pre-initialised
snapshot of the interpreter.

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
- CLAUDE.md's agent taxonomy described case workflows as module code in `app/`. Updated with
  step 2a, so the document does not describe code that is not there.
- The engine gains branch, wait, decide and durable resume. Voice and calendar runbooks get them
  too.

## Implementation steps (when accepted)

Each step is one pull request and keeps NK-01 and NK-02 green.

1. **The sandbox.** Built: the spike (D5), then the function runner in `app/functions/` with the
   limits of D5 and the helper library, the pinned interpreter fetched by
   `scripts/fetch_python_wasm.py` in the image and CI. Proof: `tests/test_function_sandbox.py`
   (NK-01 and NK-02 settle in it as in-process, with the expert's balances; every probe held;
   concurrent calls keep their own deadlines), green in CI on Linux.
2. **Definitions as data.** The blocks of D2, versioned, with export and import. Betriebskosten
   and Kaution become templates; `app/modules/` code goes. Proof: the deletion test.
   - **2a, built:** definitions on disk (`backend/definitions/<module>/<key>/`), read at boot by
     `app/kernel/definitions.py` and never imported. A case is three sandbox calls (compute,
     check, letters), about 1,5 s; the check runs in its own call so the computation cannot
     reach it. The platform's own tests run on `tests/fixtures/definitions/` (a demo definition,
     and the practice manifest, whose code is still in the platform). Proof: with
     `definitions/immobilien` deleted, the platform imports and its 933 tests pass; a test keeps
     the platform's tests from naming a shipped template.
   - **2b, built:** `definition` and `definition_version` tables. A published version is sealed
     by a database trigger: an update or delete of it is refused, whatever the code does. At
     boot the files are seeded (a changed file that keeps its version number stops the boot:
     bump it) and each definition's newest published version is loaded into the registry the
     routes read; publishing refreshes it. A workflow agent runs the newest published version;
     each case records the exact version that computed it and keeps it when a newer one is
     published. `save_draft` and `publish` are the lifecycle the Studio and the builder will
     drive (no HTTP yet: step 5); a draft that cannot run is refused at publish.
     `scripts/export_definition.py` writes a version back to files that read back to the same
     checksum. Known limit: the registry is per process, refreshed on publish; with more than
     one replica the others see a new version only at their next boot (the service runs one).
3. **Durable runs.** Run lifecycle, branch, wait, decide, approval and letters as steps; a case is
   a run. NK-02's open item becomes a wait step.
   - **3a, built:** a definition's procedure (`app/kernel/procedures.py`): steps of the kinds
     function, check and letters. Its shape is the guardrail: every function step is followed
     at once by its check, and letters come after the check of what they write from; a
     procedure that breaks either is refused at load and at publish. A definition that states
     none runs the default (compute, check, letters), derived, not stored, so the shipped
     templates' checksums did not change. A case's computation is a stored `case_run` with a
     lifecycle state (running, succeeded, failed) apart from its outcome (draft, blocked,
     failed, ready), its position, every step's output and a trace; one handler per step kind
     (`app/workflow_agents/runs.py`). Behaviour is unchanged. A run that executes within one
     request is stored with the case in one transaction, so no run can be left half done;
     recovering interrupted runs matters once a run is resumed outside its request (3b).
   - **3b, built:** the wait step. A function may return `waiting_for`: each item a field of
     the input that is empty now, what it is, who supplies it and by when. The platform checks
     that contract (no item without an addressee and a date, none for a field already filled or
     not in the form), and a function that waits with no wait step to hold it fails the run
     rather than look settled. A `wait` step (`of` a checked function step, optionally
     `resume` at a function step before it) holds the run as `suspended`: the case is
     `waiting`, its result and letters stand and can be approved and sent, and the run names
     what it waits for and when it expires (the end of the earliest due date in Germany).
     Entering the facts (`POST /cases/{id}/facts`) resumes the same run on the version it
     started on, even after a newer one was published; a letter identical to one already sent
     for the case is not offered again. Any other change to the input, or computing afresh,
     cancels the held run. An hourly sweep ends unattended waits as `expired` and blocks the
     case with a question naming who owed what by when. Betriebskosten v2: NK-02's open item is
     now a wait for `heating.hot_water_share` from the Messdienst, due 1 December 2026; once
     entered, Frau Lindqvist gets her final settlement and Herr Özdemir's unchanged letter is
     not sent twice. Every part of a run still executes inside the request (or the sweep) that
     holds the case's lock, in one transaction, so no run can be left between steps and an
     `interrupted` state is not needed yet; it is when a step acts outside the platform (send).
   - **3c, built:** decide and branch.
     - **decide.** A definition's decide step names the input fields that are a person's
       judgment: the question and the answers allowed. A function that needs one returns no
       result, only `decide: [field]`. A result computed without the judgment, a judgment the
       decide step does not name, or one already decided fails the run. The run is held and the
       case shows "Decision needed" with no figures. `POST /cases/{id}/decisions` takes one of
       the allowed answers with a reason, records who took it and why, and computes the case
       again on the run's version. The judgment is protected: the input form may keep or
       withdraw it, never set it, and a model reading documents never fills it (it is cleared
       from what the model read).
     - **Kaution v2.** The renovation-clause judgment (ADR-0036 D2) is a decide step, no longer
       a question answered in the input.
     - **Decisions do not expire, deliberately.** An expired decision would block the case with
       the same question it already shows; nothing is gained, and nothing outside the
       workspace is waited on. A wait still expires.
     - **branch.** Goes on at the step its `cases` name for a value in a checked result, or at
       `default`, or `end`. A value with no path fails the run. Jumps go forward only and never
       onto a check. The procedure check proves, over every path, that letters, wait, decide and
       branch steps see only checked results, and refuses steps no path reaches. No shipped
       definition branches yet; NK-03's letter decision is the first candidate (step 7).
     - **Known limit.** A resume that starts after a letters step (a wait whose `resume` is a
       later function) shows only the letters written after it. Every shipped procedure resumes
       at its first function, so all its letters are written again and those already sent are
       dropped.
4. **The publish gate runs the tests**, including the counter-tests, and the guardrail panel's
   fixed answers.
   - **Built.** A definition's tests are data: `cases/<name>.yaml` (input or `input_file`,
     settings, expected status, figures, letters, awaited facts and judgments, and
     counter-checks: `no_letter_matches`, `every_letter_matches`, `no_assumption_matches`).
     They are part of the version's content, so a published version names exactly the cases it
     passed, and the Studio and the builder can write them.
   - **What the gate enforces.** The rules of ADR-0038 D4 are enforced mechanically: every case
     names its source and every expected figure where it was copied from (`ref`); a definition
     with no case, or with no counter-check, is not published.
   - **How it runs.** The gate (`app/definitions/gate.py`) runs every case as a real case runs:
     through the procedure, functions in the sandbox, the check and the letter check. It is
     not pytest on the host, which a definition written in the Studio could not safely be
     given. Publishing a draft and seeding a new version at boot both run it; any failure
     refuses the version and names the case, the path, the value and the ref. The report is
     sealed with the version (`definition_version.gate`, migration e0f4a5b6c7d8), and
     `definitions/tests/test_publish_gates.py` gives CI the same proof before a deploy.
   - **Guardrail panel.** Computed for the version an agent runs (checks, letter check,
     approval, tests passed, a person's judgments, rules version, nothing touched outside); on
     the agent's API and page, read-only.
   - **New versions.** Betriebskosten v3 (NK-01, NK-02 from the expert's sheets), Kaution v3
     (KA-01, KA-02 worked by hand and marked so, no expert sheet yet, and KA-02 with the clause
     undecided). The developer's pytest gold tests stay; the cases do not replace them.
5. **The Studio.** Create from a template, edit the draft in step forms, see the guardrail panel,
   run tests, publish. Decided (3 November 2026): a workspace may edit the data and the function
   code by hand; code runs only in the sandbox and is published only through the gate.
   - **5a, built:** a workspace owns the definitions it builds. `definition.tenant_id` (none for
     the shipped templates) and `template_key`, migration f1a5b6c7d8e9. `/api/v1/definitions`:
     - list the templates and the workspace's own definitions; copy a template into a private
       draft (a new key, so nothing points at the template)
     - read the published version, the draft, the guardrails and the history
     - save the draft (any member), run its cases without publishing, discard it, and publish
       through the gate (administrators only)

     A shipped template is read by all and changed by none (403: copy it first); another
     workspace's definition is a 404. A workspace's published definitions are offered for its
     agents and nobody else's. A draft is untrusted input: bounded at 2 MB, its file names
     checked by the bundle, its code run only in the sandbox (a test proves a function reading
     `/etc/passwd` fails the case and is not published).
   - **5b, built:** the Studio screens to read, test and publish.
     - **`/studio/definitions`:** the workspace's own workflows, and the templates, each read
       there and copied into a private draft.
     - **`/studio/definitions/[key]`:** the steps in plain words, what the draft changes against
       the published version, the test cases with Run tests and each case's failures, the
       guardrail panel, rules and code read-only, and the history. Publish and Discard draft
       sit on the draft.
     - **Studio home:** "Build a workflow" beside "New workflow agent".
   - **5c, built:** the editors, on a workspace's own draft.
     - **Tabs:** Overview (name, summary, reading guide), Rules, Steps (add, remove, reorder,
       each step's fields), Code (a monospace editor per file; add and remove files), Tests (each
       case, add and remove), and Forms.
     - **One draft per page:** saved with "Save draft". Run tests and Publish work on the saved
       draft and wait while there are unsaved changes. A published workflow without a draft has
       "Edit as new draft", the next version; the published one keeps running until it is
       replaced.
     - **No editor dependency:** text areas, with JSON checked before it is saved. Everything
       else is the server's to refuse: the gate, the sandbox, the bounds.
6. **The builder.** Draft a definition from a description or from documents. Decided (4
   November 2026): instruct, then revise (each instruction one recorded run); the provider
   document reading already uses, behind a protocol (`BUILDER_MODEL`); and a self-test loop of
   at most four rounds.
   - **6a, built:** the builder's core (`app/builder/`).
     - **A run** (`builder_run`, migration a2b6c7d8e9f0) takes an instruction and the
       workspace's documents. The model answers with a change (format in `guide.md`, the
       builder's versioned prompt). The platform applies it, checks it, and runs every case
       through the gate in the sandbox. Failures go back to the model, with "fix the code or the
       rules to fit the norm, never an expected figure". The last applicable change is saved as
       the draft, with the summary, each round's report and the guardrail panel's changed
       answers.
     - **Where it runs:** after the request (`POST /definitions/{key}/builder`, then poll
       `GET`), one run per definition at a time. A run a restart cut off is marked interrupted
       at boot.
     - **Governance is the absence of capability:** the builder has no way to publish, to touch
       a shipped template or another workspace's definition, to change key, module, version or
       `shared/`, to confirm a figure, or to run code outside the sandbox.
     - **ADR-0038 D4, mechanically:** every expected figure the builder adds or changes is
       marked `unverified`, whatever it answers. The gate refuses to publish while one remains,
       and `POST /definitions/{key}/draft/confirm` is a person confirming one against its
       source, recorded as `confirmed_by`.
     - **Tests** use a scripted model: what the builder may change, the forced flags, the
       feedback loop, the bound, failures recorded, the absence of publishing.
   - **This is not the task tier.** A run is a bounded loop whose only effect is a draft; the
     model plans no actions in the world.
   - **6b, built:** the Studio panel on a workspace's own workflow
     (`features/definitions/BuilderPanel.tsx`): an instruction, documents chosen from the
     workspace's files or uploaded, the runs refreshed while one is active, each with its
     summary, every round's test results and the guardrail answers that change. Figures the
     builder wrote are listed with their case's source and reference, each confirmed by a
     person. **While a run is queued or running, the draft is not changed by hand:** saving,
     confirming, discarding and publishing are refused with 409, because the run saves the
     draft when it ends and an edit made meanwhile would be lost unseen.
   - **6c, done (7 November 2026):** the real model (`gpt-5.5`) on NK-01, through
     `scripts/builder_trial.py` (a local database only; it refuses any other).
     - **Trial A, write a case:** the template without `nk01`, the task, the expert's sheet and
       the review attached. One round, under a minute: the input it read from the PDF matched
       the shipped case field for field. All six shipped figures came out the same, and 31
       more, each with its place in the sheet and each unverified. It added two
       counter-checks of the review's own on its own.
     - **Trial B, fix the cause:** a bug planted in `calculate.py` (small repairs no longer held
       back). First run: it named the cause correctly, left every figure alone, and passed,
       but by copying the broken function into `compute.py` and swapping it in over the
       original. The bug stayed where it was, now twice. **Passing cases did not show it.**
       So a file the builder writes may no longer set, replace or delete a name of another
       module (`apply.py`, refused and told why; rule 3 of `guide.md`). Run again: it fixed the
       one line in `calculate.py`, back to the shipped text.
     - **What this says:** on a case whose figures an expert has written down, the builder is
       good. What the gate cannot see is how a passing change is made, so a person reviews
       code changes as well as figures. The hard test is step 7: NK-03 and NK-04, where the
       code itself must change and no sheet is shown to it.
7. **Acceptance.** NK-03 and NK-04 built through the Studio and the builder, with no platform
   change. The runs' answers go to the expert as unverified snapshots (ADR-0038 D4).
