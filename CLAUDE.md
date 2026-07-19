# CLAUDE.md: Field Notes on Getting a Language Model to Write Code You Will Not Rewrite

## A Short List of Rules, Earned by Watching the Same Mistakes Twice


### Abstract

This file exists because language models make predictable mistakes when they write code. Not random mistakes, just the same ones, over and over, often enough that it was worth writing them down. What follows is not a set of suggestions but a set of rules. The throughput is the same in every section: the model is fast at generating plausible code and slow to notice that plausible is not the same as correct, so the discipline has to come from the process around it.

### Index Terms

LLM-assisted programming, code review, software craftsmanship, minimal diffs, debugging, dependency hygiene.

---

### 0. Design Principles — and Where Each Is Enforced

These are not aspirations; each maps to a concrete, checkable mechanism already in this codebase — a change that violates one is wrong by definition. When you add code, say which layer and domain it belongs to (in the PR or a comment); if it fits none, the boundary is wrong — fix the seam, don't smuggle logic across it (§XII). The enforcement column is deliberately specific to *this* repo; if you ever find it describing code that isn't here, the doc is stale — fix it, don't code to a fiction.

| Principle | How it is enforced here (not just claimed) |
|-----------|--------------------------------------------|
| **Separation of concerns** | Layers stay in their lane: `app/api/v1/*` is HTTP only (routing, auth deps, status codes) → domain logic in `app/sync/`, `app/connections/`, `app/providers/` (**no FastAPI imports**) → `app/db/models.py` + `app/db/repo.py` own persistence (the only place queries live) → `app/schemas/*` are the wire contracts. A router never builds a query or holds business logic; a connector/orchestrator never touches `Request`/`Response`. |
| **Encapsulation** | The governed loop (precondition → allowlist → approval → execute → postcondition → trace) lives in the laufwise engine and is **closed for modification** (§XII). Domain behaviour plugs into named seams only. The write path is **sealed**: no `update`/`delete` capability exists anywhere — append-only is enforced by the *absence* of the method (ADR-0004 D7), and a tool's return value never stands in for truth; the postcondition decides by re-querying real state. |
| **Abstraction / dependency inversion** | Volatile systems sit behind stable protocols; callers depend on the interface, not the transport: `SourceCalendar`/`DestinationCalendar` + `build_connector` (healthyfeet REST, thevea GraphQL, doctolib headless — the runtime can't tell which); the `StateProvider` protocol + `RoutingStateProvider`; base URLs, credentials and keys injected via `config.settings`, never hard-wired. You add a system by writing a provider and registering it — never by editing the engine or the runner. |
| **Single source of truth** | Governed processes are **data**, defined once: a runbook YAML in `runbooks/` (references *roles*, not adapters). The connector registry is one place (`build_connector` + `_DEFAULT_BASE_URL`/`_base_url_for` — keep those in sync). A published template version is immutable; the seed skips any existing `(name, version)`, so you bump the version, you don't mutate in place. |
| **Single responsibility** | One module, one job: a connector talks to exactly one system (its auth/parsing quirks stay *inside* it); the orchestrator enumerates + classifies; a provider exposes one system's reads to the engine; a workload is the only place an executor acts. Each has a narrow I/O contract and is tested against a mock transport, not the network. |
| **Fail-closed / least surprise** | Unavailable or ambiguous state never slips into the record: a source read failure raises `StateUnavailable` → the engine **BLOCKs** (never a false "absent" that could double-write). A binding routed to a real provider is **never** served from the in-memory fixture (anti-fabrication, ADR-0003 D4). The publish gate makes an ungoverned template unrepresentable. Tenant isolation is on by default — every connection/instance/job query is scoped by `tenant_id`; an unowned id is a 404, not a leak. |

---

### I. Read Before You Write

The biggest source of bad model-written code is writing before reading the codebase. Read the files you are about to touch; read, not skim. Copy the patterns that already exist, and check the imports to see what the project actually depends on, so you do not reach for axios where everything is fetch. When you cannot find a pattern, ask instead of guessing.

---

### II. Think Before You Code

Figure out what you are doing before you type. State your assumptions ("add authentication" is five different things, so name the one you picked) and name the tradeoffs. If something is genuinely confusing, stop and ask rather than filling the gap with plausible-looking code; that is exactly the code that passes a casual review and fails when it matters.

---

### III. Simplicity

Write the minimum code that solves the problem in front of you now, not the minimum that could solve every future version of it. Resist premature abstraction, skip error handling for errors that cannot occur, and hardcode values until there is a real reason to configure them. The test: if the only reason something is abstracted is "in case we need to," you have over-built it.

---

### IV. Surgical Changes

Your diff should be as small as the task allows. Do not touch what you were not asked to touch, match the existing style, and do not reformat; a formatter pass buries the three lines that matter inside three hundred that do not. The test is whether you can justify every changed line by the task. If a line is there because "while I was in there," revert it.

---

### V. Verification

The gap between code that works and code you think works is testing. When fixing a bug, write the failing test first, watch it fail, then fix it; that is the only proof you fixed the cause and not the symptom. Test behavior that can actually break, not that a constructor sets a field. If something is hard to test, that is information about the design, not permission to skip it.

---

### VI. Goal-Driven Execution

Every task needs a success criterion before code is written. "Add validation" becomes "reject a missing or malformed email, return 400 with a clear message, and test both cases." For anything multi-step, state the plan first so the user can catch a wrong approach before you spend an hour building it.

---

### VII. Debugging

When something breaks, investigate; do not guess. Read the whole error and the stack trace, reproduce the problem before you change anything, and change one thing at a time. Do not paper over an unexpected null with a null check; find out why it is null, or the bug just moves somewhere quieter.

---

### VIII. Dependencies

Every dependency is permanent code you do not control. Before adding one, ask whether the project or the standard library can already do it with crypto.randomUUID() over a uuid package. When you do add one, say why, so the choice is visible rather than smuggled into the manifest.

---

### IX. Communication

Say what you did and why, not just a block of code. Flag concerns even when you did exactly what was asked, and be precise about uncertainty: "I am not sure this library supports streaming" tells the user what to verify; "I think this should work" does not.

---

### X. Common Failure Modes

A few patterns recur often enough to name: the Kitchen Sink — restructuring half the codebase while you are in it; the Wrong Abstraction — copy-paste twice before you abstract; the Optimistic Path — the happy path handled and the 500 ignored; and the Runaway Refactor — a fix that cascades across files. Catch yourself in any of these and the right move is to stop, not to push through.

---

### XI. Responsive & Full-Bleed UI

Build every screen for both phone and desktop from the start, not as an afterthought. The layout fills the viewport — `min-h-screen`, full-bleed backgrounds — while the content stays in a readable max-width column. Use mobile-first responsive utilities: stack on small, expand on large. The test: nothing overflows horizontally at a 375px width, and nothing looks cramped or lost on a wide desktop. Decorative visuals scale with their container; no fixed pixel sizes that break on small screens. A visual should mean something — prefer an illustration of the actual system over abstract noise.

---

### XII. Extending the Platform — Seams, Not Surgery

The whole point of this codebase is that you add capability by writing a *plugin* or a *data file*, never by editing the engine. The governed loop (precondition → allowlist → approval → execute → postcondition → trace) lives in laufwise and is closed for modification. Everything domain-specific plugs into a named seam. Before adding a feature, find its seam; if you think you need to change the engine or the runner to add a domain behavior, you have the wrong seam.

- **Add a source system** (read): implement the `SourceCalendar` protocol (`list_appointments`, `get_appointment`, `close`) in `app/providers/<name>.py`, then register the adapter in `build_connector` (`app/connectors/base.py`) and its base URL in `config.py` + `_base_url_for`. Keep the system's quirks (auth, HTML/JSON parsing) *inside* that file — nothing above the connector should know whether the source is REST, GraphQL, or scraped HTML. (healthyfeet parses `data-booking` JSON out of an HTML page; thevea speaks GraphQL; the runtime can't tell.)
- **Add a destination** (write): implement `DestinationCalendar` (`find_appointment`, `create_appointment`, `close`). Note the deliberate omission — there is **no `update`/`delete`**; append-only is enforced by the *absence* of the capability (ADR-0004 D7). Don't add mutation methods to make a feature easier; that deletes a guarantee.
- **Add a governed process**: write a template YAML in `runbooks/`. Reference **roles** (`source`/`destination`), not concrete adapters, so the template stays reusable; the instance binds roles to connectors. The publish gate makes an ungoverned template unrepresentable — lean on it.
- **Add a tool** (the only place an executor acts): a callable in `app/workloads/`, wired through the `ToolRegistryAdapter`. It claims success; the postcondition decides truth by re-querying real state. Never let a tool's return value stand in for verification.
- **Add an LLM agent** (not built yet, M2): it plugs in at the `ExecutionAdapter` seam / `RunbookMcpServer`, not into the runner. A multi-model "model factory" (provider config + keys via env) belongs at that agent layer — build it when a step needs a model, not before (§III). The current workflow agents make **zero** model calls, and that is a feature: deterministic, verifiable, free.

The test of every extension: could you delete it and the engine still compiles and passes? If the engine depends on your addition, the seam is wrong.