# ADR-0039 — A solution is a package on one engine: the platform owns the step kinds, a package owns the domain

Status: proposed · 1 November 2026 · not built · builds on ADR-0028 (modules), ADR-0030
(workflow agents and cases), ADR-0033–0035 (reading, letters, approvals), ADR-0038 (rules packs);
changes where ADR-0030's workflows live and how they run

## Context

The platform should run any back-office agent a small business needs, not one hand-built agent
per use case. Today it does the opposite in one place.

**Two workflow systems that do not meet:**

1. **Runbooks** (`backend/runbooks/*.yaml`, `runner.py`): a generic, data-defined step graph. It
   has the governed loop (precondition → allowlist → approval → execute → postcondition → trace),
   tools and a trace. Voice and the calendar import run on it. It has no branch, no wait, and no
   durable suspend and resume (`workflow-procedures` skill, "What to add").
2. **Case workflows** (ADR-0030, `app/modules/*/<workflow>/`): forms derived from schemas, reading
   documents into them, the letter check, approvals. All generic, and the Kautionsabrechnung proved
   it (ADR-0036). But each workflow is Python inside the platform, imported by name
   (`INSTALLED`), with one fixed shape: parse → compute → check → letters. ADR-0030 D6 and
   ADR-0035 D5 both say "not through the engine yet".

**What that costs, shown by the test series** (`docs/use_cases/real_estate/bo/`):

- Every new use case is platform code and a platform deploy.
- **The fixed shape does not fit the next cases.** NK-03 is two procedures (a settlement and a rent
  increase under § 558) with one decision about their letters. NK-04 is a deadline analysis, then
  a person's decision, then per-position consequences and a recommendation.
- **Generic capabilities get built inside one workflow.** Making NK-02 pass (commit 79b564a) needed
  "one fact is missing: name it, who supplies it, by when; the rest stands; continue when it
  arrives". That is a wait step any procedure needs. It now lives in `betriebskosten`, where no
  other workflow can use it.

**Prior art.** Wonderful separates the two cleanly. A solution is a repository of data and small
code: a procedure as a graph of generic steps (trigger with an input schema, branch, llm, agent,
approval, tool), functions, table schemas, and a governance file the customer uploads, which is
validated, immutable once stored, and activated with an owner and a start date
(`work_projects/wonderful/repos/adac-campaign-planner`). The platform knows nothing about
campaigns.

## Success criterion

1. The platform contains no domain code. **Test:** delete every package, and the platform still
   builds and its own tests pass (CLAUDE.md § XII).
2. **NK-03 and NK-04 are delivered as packages, in pull requests that change no platform file.**
   Once the platform has the steps they need, this is the measure of whether it is a platform.
3. NK-01 and NK-02 give the same results as today (`test_betriebskosten_gold.py`) after the move.

## Decision

### D1 — Two layers, and the line between them

| | Platform | Package |
|---|---|---|
| **Owns** | the step kinds, the engine, runs and cases, forms from schemas, reading documents, the letter check, approvals, mail, the trace, evals runner | one solution's procedure, functions, rules, schemas, wording, eval cases |
| **Knows** | what a step, a form, a letter and an approval are | what a tenancy, a Betriebskosten position and § 9b are |
| **Changes when** | a new kind of step is needed by more than one package | the domain needs it |

A module (ADR-0028) stays the grouping of packages that share nouns and a Studio pitch
(`immobilien`: Betriebskosten, Kaution, Mieterhöhung). Code several packages of a module share
(German euro format, salutations) is a module library. It is not platform code.

### D2 — What a package is

A directory with a manifest. Everything the platform reads from it is data, apart from functions:

```
packages/immobilien/betriebskosten/
  package.yaml        name, version, module, label, summary, which rules pack
  procedure.yaml      the steps, in the platform's step kinds (D3)
  schemas/            input, settings, result: JSON Schema, with labels and choices
  functions/          deterministic code: compute, check, letters (D4)
  rules/              the rules pack, versioned (ADR-0038)
  guide.md            how to read the documents into the input (today's extraction_guide)
  evals/              cases: task fixture, expected results with their source (ADR-0038 D4)
```

The platform reads schemas as data and never imports a package's types. A package written in
Python may generate its schemas from its dataclasses; a check in CI keeps them in sync. Versions
are immutable once published, as templates are today. A change bumps the version, and a case
keeps the version it was computed with.

### D3 — The step kinds the platform owns

One engine runs every procedure. A case workflow becomes a procedure, and the case is its run.

| Step kind | Does | Today |
|---|---|---|
| **input** | starts the run from a form (the trigger) | the case input |
| **read** | a model fills the form from documents, a person confirms (ADR-0033) | built, per workflow |
| **function** | runs a package function on the input; may return questions, assumptions, open items | `compute` |
| **check** | the postcondition, always run after a function | `check`, always run |
| **branch** | goes one way or another on a value of an earlier step | missing |
| **wait** | parks the run until a named fact arrives from a named party by a date, or until a date passes | missing; built inside `betriebskosten` (Context) |
| **decide** | parks the run for a person's judgment with a reason (ADR-0036 D2, NK-04's responsibility for a late settlement) | built as an input field |
| **letters** | writes texts from a ready result, checked against it (ADR-0034) | `drafts` |
| **approval** | a second person approves exactly what is sent (ADR-0035) | built |
| **send** | mails it | built |
| **llm / agent** | AI where a package wants it, as one step | missing; not needed by NK-01–04 |

A wait or a decision parks the run durably: persisted, resumed by id after a restart, expired when
unattended (`workflow-procedures` skill). The governed loop of enforced steps stays closed for
modification. These kinds are added beside it, not inside it.

**The NK-02 open item moves into the platform.** A function returns an open item (what, from whom,
by when, the method that applies once it arrives, the part of the result it holds open). The
platform parks that part of the run, tracks the date, shows it, and resumes when the fact is
entered. The package still writes the request letter, because its wording is domain.

### D4 — Functions are pure

A function gets JSON in and returns JSON out. It does no I/O and calls no model. The platform
supplies everything it needs, including the active rules pack and the dates. That keeps results
reproducible from the input and the rules version, and it lets the platform run functions isolated
later (D6) without changing them. The module workflows already work this way.

### D5 — Rules are activated per workspace, not just shipped

The rules pack of ADR-0038 is the package's governance file, with Wonderful's lifecycle: stored
immutable, validated, with an owner and a start date. A workspace runs the version that is active
for it. ADR-0038 D2 and D6 are unchanged: no editor, no per-workspace rewriting, and an
unreviewed pack says so.

### D6 — Loaded at boot first, uploaded later

**Stage 1:** packages are directories in the repository, discovered at boot from their manifests.
No platform file names one, and `INSTALLED` lists modules only until the registry discovers them
too. A new package is a pull request inside `packages/` and a deploy, with no platform change.

**Stage 2, not now:** packages uploaded at run time, with functions run in a sandbox. That brings
real costs (isolating code, quotas, a store for versions). It is built when someone outside this
team writes packages. Until then a coding model writes them from an expert's documents
(ADR-0038 D3), and review happens as a pull request.

## Not building

- Runtime upload and sandboxed functions (D6 stage 2).
- A visual procedure editor. A procedure is a file, reviewed like code.
- The llm and agent steps, until a package needs one. NK-01 to NK-04 need none.
- Webhook and schedule triggers. A case starts by hand.
- Migrating the voice and calendar runbooks into packages. They already run on the engine; they
  move when that buys something.

## Consequences

- The engine gains branch, wait, decide and durable resume. Those are the items the
  `workflow-procedures` skill already lists first. Voice and calendar runbooks get them too.
- The `Workflow` dataclass in `app/kernel/workflows.py` is replaced by the package manifest and
  procedure. The four functions survive as functions.
- CLAUDE.md § 0 and § XII describe `app/modules/` as where domain logic plugs in. They are updated
  when this is built, so the document does not describe code that is not there.
- ADR-0038 becomes this design's rules part. Its decisions stand.

## Implementation steps (when accepted)

Each step is one pull request and keeps NK-01 and NK-02 green.

1. **Package format and loader.** Move `betriebskosten` and `kaution` into `packages/immobilien/`
   unchanged in behaviour: schemas as data, the four functions behind a manifest, a procedure that
   spells out today's fixed shape. Proof: the deletion test of the success criterion.
2. **Durable runs: wait and decide.** Run lifecycle beside the outcome, park and resume by id,
   expiry. The NK-02 open item moves from the package into the platform's wait step.
3. **Branch and multi-step procedures.** Several functions in one run, letters from more than one
   result. NK-03 needs this.
4. **NK-03 as packages:** Part A in `betriebskosten` (the lease names its costs), Part B as
   `mieterhoehung`, Part C as one procedure step deciding the letters. No platform file changes.
   The run's answer goes to the expert as an unverified snapshot (ADR-0038 D4).
5. **NK-04 as a package:** deadline analysis with a decide step for whether the landlord is
   responsible for the delay. No platform file changes.
