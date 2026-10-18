# Building the modular platform — step by step

Status: plan · 18 October 2026 · implements [ADR-0022](../adr/0022-modules-one-kernel-many-workspaces.md)
(proposed). Paths are relative to `backend/` or `frontend/`. Sizes: **S** one PR, **M** 2–3 PRs,
**L** 4+ PRs.

**The voice gate** — every step that touches `app/workloads/conversational/` merges only with
`tests/test_voice_eval_runner.py`, the `evals/scenarios.json` replay and `studio_agent.json`
green; steps marked *shadow* also need a test call on a staging number before merge.

Unverified going in: the laufwise engine's `ApprovalGate` signature (`laufwise/approval/base.py`,
`engine/local.py`) — step 6a depends on it.

## Order

| # | Step | Size | Touches the live voice path? |
|---|---|---|---|
| 0 | Pin skill prompts + tool descriptions into the snapshot | S | yes (read path) |
| 1 | Kernel facade + boundary test | S | no |
| 6a | Real approval gate | M | no (voice contracts may not carry approvals) |
| 2a | System registry | M | via `calendar.py` — voice gate |
| 2b | Process + orchestrator registries | M | no |
| 3a | Agent config split by inheritance | S | `instance_config` — voice gate |
| 4 | `workspace_module` + backfill (backend) | S | no |
| 6c | File storage | M | no |
| 6b | Task tier | L | no |
| 2c | Conversation kit | M | **yes — voice gate + shadow** |
| 3b | Move the vertical into `modules/praxis` | L | **yes — voice gate + shadow, per PR** |
| 5 | Frontend section registry | M | no |
| 7 | `modules/immobilien` | L | no |

Step 7's *discovery* (the customer, their CRM and portal, what each API lets us re-read) starts in
parallel with step 1. Steps 0, 1 and 6a are worth doing even if nothing after them is.

---

## Step 0 — Make a published agent actually immutable (S)

**Why:** `make_snapshot` (`app/agents/service.py:164-171`) pins knowledge, contracts and the base
prompt, but `surface._instructions` reads skill prompts from disk on every call (`surface.py:231`)
and tool descriptions come live from `booking.TOOLS`. A module whose data version can be pinned
(ADR-0022 D6) needs this first.

**Change:** the snapshot gains `"skills_pinned": {name: prompt_text}` and
`"tools_pinned": {name: {description, properties}}`. `_instructions` and `_booking_tools` prefer
the pinned values; a snapshot without them (every agent published before this) falls back to disk,
exactly as today. `instance_config` pops the two new keys like it pops `knowledge`.

**Test:** publish an agent, edit its `skill.md` in a tmp skills dir, build the call — the
instructions are unchanged; an old snapshot without the keys still builds identical instructions.

## Step 1 — Kernel facade and boundary test (S)

**New:** `app/kernel/__init__.py` (re-exports only — the module API `kernel_api` versions),
`modules/__init__.py`, `modules/praxis/{__init__.py,module.yaml}` (empty registration),
`MODULES = ["praxis"]` in `app/config.py`, `tests/test_module_boundary.py`.

```python
# tests/test_module_boundary.py — sketch
KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset({...})  # (importer, imported), today's

def test_kernel_never_imports_modules_and_modules_only_import_the_kernel():
    found = {edge for edge in _import_edges(ROOTS) if _forbidden(edge)}
    assert found <= KNOWN_VIOLATIONS, f"new boundary violations: {found - KNOWN_VIOLATIONS}"
    assert KNOWN_VIOLATIONS <= found, f"fixed — remove from the list: {KNOWN_VIOLATIONS - found}"
```

`_forbidden`: `app.*` (other than `app.kernel`) importing `modules.*`; `modules.*` importing any
`app.*` other than `app.kernel`; `modules.<k>.*` outside `modules.<k>.systems` importing `httpx`,
`requests` or `playwright` (ADR-0022 D4c). `ast` only, no dependency.

## Step 6a — A real approval gate that respects the two clocks (M)

**Today:** `control_plane/factory.py:45` wires `AutoApprovalGate`; `api/v1/approvals.py` returns
`[]`; `templates/compiler.py:11-15` lets `required_when` fall through to auto-approve; `approval`
(`db/models.py:177`) has no `tenant_id`.

**Change:**
- Migration: `approval` gains `tenant_id`, `instance_id`, `template_name`, `step_id`,
  `case_fingerprint` (sha256 of the canonical case), `requested_payload`.
- `app/control_plane/approvals.py`: `PersistedApprovalGate(decisions)`. The engine gate is called
  synchronously inside the run, with no DB session — so decisions for this instance are *preloaded*
  into a dict keyed `(step_id, case_fingerprint)` before the run starts. No decision → the request
  goes to an outbox and the gate denies with `approval_pending`. `factory.py` takes the gate as a
  parameter instead of constructing `AutoApprovalGate`.
- `Runtime.run_instance` persists outbox rows and marks the run's lifecycle `awaiting_approval`
  (lifecycle, not the governance outcome — the `ImportJob.status` comment's rule).
- `POST /approvals/{id}/decide` records approver + timestamp, then **re-runs** the same instance
  with the same case. Preconditions re-check real state, so the steps before the gate cannot
  double-write — resume by re-execution, no engine change.
- Publish gate: a contract pinned by a conversational agent may not contain an approval step.
- `required_when` with field expressions: lower onto the engine's `risk >= level` if the compiler
  can (ADR-0022 open question 2); otherwise it is an engine change and gets its own ADR.

**Tests:** a denial BLOCKs; an approval re-run writes exactly once; real state changed between
request and approval → BLOCK at the precondition; deciding another tenant's approval → 404;
publishing a voice agent whose contract has an approval step → refused.

## Step 2 — Registries (M each)

```python
# app/kernel/registry.py — sketch
@dataclass(frozen=True)
class SystemSpec:  # replaces build_connector, _DEFAULT_BASE_URL, _ADAPTER_BASE_URL, VOICE_CALENDARS
    adapter: str
    label: str
    connection_type: str
    base_url: Callable[[], str]                  # a setting the module declares
    build_read: Callable[[str, dict, dict], Any]
    build_write: Callable[[str, dict, dict], Any] | None = None   # only wire() may call (D4b)
    ports: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()   # module vocabulary; take-away-only (ADR-0014)
    verify: Callable[[Any, BaseModel], None] | None = None
    mapping: Mapping | None = None

@dataclass(frozen=True)
class ProcessWiring:  # replaces resolve.py:197-238
    template: str
    roles: tuple[str, ...]
    wire: Callable[[dict[str, Any], dict], Wired]  # clients by role + case -> providers + tools

class KernelRegistries:
    systems: Registry[SystemSpec]
    processes: Registry[ProcessWiring]
    orchestrators: Registry[Orchestrator]          # sync/jobs.py:35-42
    conversation: Registry[ConversationKit]        # step 2c
    agent_configs: Registry[type[KernelAgentConfig]]
    runbook_dirs: list[Path]; skill_dirs: list[Path]; preset_dirs: list[Path]
    sections: list[SectionSpec]
    def freeze(self) -> None: ...                  # duplicate key or template name -> boot error

class Module(Protocol):
    key: str
    kernel_api: str
    def register(self, kernel: KernelRegistries) -> None: ...
```

`app/main.py` and the scheduler entrypoint call `load_modules(settings.modules)` once.

- **2a systems:** `build_connector` becomes a registry lookup with the same signature, so tests
  that monkeypatch it keep working; both base-URL maps collapse into `SystemSpec.base_url`;
  `VOICE_CALENDARS` entries become `SystemSpec`s registered by praxis. Test: `test_connectors.py`
  and `test_practice_systems.py` pass unchanged.
- **2b processes + orchestrators:** the role wiring in `resolve.py` and the `_ORCHESTRATORS` map
  move behind registries; `db/seed.py` iterates every registered runbook dir and records each
  template's `module`; `make_snapshot`'s three hard-coded contract names come from the module.
  Test: a fake module registered inside a test runs a governed run without touching a kernel
  file — an early proof of criterion 1.

## Step 3a — Agent config split by inheritance (S)

`KernelAgentConfig` (name, locale, voice_id, voice engine, greeting, instructions, skills,
recipients, transfer_number, knowledge ids, transcript retention, recall policy) and
`PraxisAgentConfig(KernelAgentConfig)` = today's `AgentConfig`, field for field, still
`extra="forbid"`. Storage stays flat; new snapshots carry `"module": "praxis"`; a missing key means
praxis. `instance_config` dispatches on that key and otherwise behaves exactly as today. The
Studio's schema comes from `PraxisAgentConfig.model_json_schema()`.

**Test:** every fixture in `tests/test_agent_compatibility.py`, plus an export of production
`agent_instance.runtime_config` rows, parses to an identical config before and after.

## Step 4 — `workspace_module` (S, backend)

```
workspace_module(id, tenant_id FK, module_key, data_version, config JSONB, status,
                 installed_at, UNIQUE(tenant_id, module_key))
studio_agent.module_key  default 'praxis'
template.module, template.tenant_id NULL  + two partial unique indexes (ADR-0022 D3)
```

Backfill one `praxis` row per existing tenant, so ADR-0016 agencies see nothing change.
`GET /workspace/modules` returns installed modules and their `SectionSpec`s. Tenant-scoped like
every other query; an unowned id is a 404.

## Step 6c — File storage (M)

`app/kernel/files.py`: `BlobStore` protocol (`put(tenant, data, mime) -> FileRef`, `open(ref)`,
`signed_url(ref, ttl)`), a Supabase Storage implementation and a local-disk one for tests. Table
`stored_file(id, tenant_id, sha256, mime, size, key, created_by, created_at)`, append-only;
removal only by a retention sweep (the `conversational/retention.py` pattern). Tests: tenant
isolation; dedupe by sha256 is per tenant, never across tenants.

## Step 6b — The task tier (L), per ADR-0006 D2/D3/D6

- `app/workloads/task/{runner.py,protocol.py}`; `agent_class` gains `task`, with its own
  publish-gate rules.
- `task_set_status` is the only way a model changes lifecycle; `tasks/state.py` gains D3's rules
  (a pause needs a proposed action **and** a reason; a resume needs an approver and a timestamp; a
  switched-off capability removes the tool *argument*).
- The model's consequential tool calls dispatch `Runtime.run_instance` — every one is a governed
  run.
- Clock: the existing scheduler process (`sync/scheduler.py`) leases `pending`/`live` task rows
  (Postgres only, no Temporal — PLATFORM_PLAN staged-infra rule). An approval decision (6a) wakes
  its task.

**Tests:** illegal transitions refused; a disabled pause is absent from the tool schema; a restart
mid-task resumes from `TaskEvent`s.

## Step 2c — The conversation kit (M, voice gate + shadow)

```python
class ConversationState(Protocol):   # what surface.py touches today
    caller_turns: int
    def summary(self) -> dict: ...
    def take_executions(self) -> list[ExecResult]: ...
    def memory_projection(self) -> dict | None: ...

class ConversationKit(Protocol):
    module: str
    def resolve_resource(self, instance, connections) -> object: ...   # today resolve_calendar
    def new_state(self, call_id: str, *, resource: object, config: KernelAgentConfig,
                  contracts: dict | None, test_mode: bool) -> ConversationState: ...
    def tools(self) -> Sequence[ToolSpec]: ...            # ToolSpec.call(state, args)
    def instructions(self, config, now) -> str: ...       # appended after the kernel's rules
    def tool_properties(self, spec, config) -> dict: ...  # e.g. the treatments enum
    def can_transfer_now(self, config, now) -> bool: ...  # today: opening hours (:551)
    async def deliver_summary(self, summary, **context) -> dict: ...
```

Out of `surface.py` and into praxis's kit: `_instructions` lines 240–271, `tool_properties`
(426–442), the opening-hours transfer gate, the inline `BookingSession(...)` (774–783),
`send_call_summary(practice=...)`, `memory_projection`, `prompts/studio.md`. `booking.py:1437`'s
import-time `load_practice()` must become lazy first. The generic remainder stays in the kernel.

## Step 3b — Move the vertical into `modules/praxis` (L, voice gate + shadow per PR)

Pure moves plus import updates, one family per PR: `systems/` (thevea, healthyfeet, doctolib, the
two practice calendars); `ports.py` (`Appointment`, `Patient`, `PracticeCalendar`,
`AppointmentLifecycle` out of `connectors/base.py`, split read/write); `conversation/` (booking,
practice, calendar, capabilities, skills, `studio.md`); `runbooks/`; `presets/`
(`practice_types`); `orchestrators.py` (sync orchestrator + mirror, given read views — D4b);
`evals/`. `ImportJob` stays where it is, listed as a grandfathered module table; it converges into
`Task` later (ADR-0006 D7). The hard-coded room ids (`runbooks/calendar_import.yaml:34`,
`sync/orchestrator.py:293`) move into connection config.

**Test:** the boundary list reaches zero; with `MODULES=[]` the kernel imports and its tests
(everything outside `modules/praxis`) pass — criterion 2.

## Step 5 — Frontend section registry (M)

```ts
// frontend/src/modules/registry.ts
export type SectionKind = "config" | "records" | "runs" | "approvals" | "custom";
export interface SectionSpec {
  module: string; key: string; title: string; group: string;
  scope: "workspace" | "agent"; kind: SectionKind; source?: string; component?: string;
}
export const CUSTOM: Record<string, ComponentType<SectionProps>> = {
  "praxis.agent_config": dynamic(() => import("./praxis/ConfigEditor")),  // moved, unchanged
  "praxis.phone": dynamic(() => import("./praxis/PhoneSetup")),
};
```

`WorkspaceShell.tsx`'s navigation becomes kernel items + `useInstalledModules()`; a new route
`app/studio/m/[module]/[[...section]]/page.tsx` serves workspace sections; the agent route's
hard-coded section allowlist and `AgentWorkspace.tsx`'s `GROUPS` come from the agent's module; an
unknown section is `notFound()`. A generic `SchemaForm` (string, int, bool, enum, array of objects,
`x-ui` hints) is built for immobilien; praxis keeps its hand-built editor. Test: a workspace with
only immobilien installed shows no "Phone & handoff".

## Step 7 — `modules/immobilien` (L, against one named customer)

**Ports** (`modules/immobilien/ports.py`), read and write split, every mutation named (D9):

```python
class CrmSource(Protocol):          # onOffice / FlowFact / Propstack behind it — unverified
    def list_properties(self, since: str | None) -> list[Property]: ...
    def get_property(self, ref: str) -> Property | None: ...
    def find_contact(self, email: str | None, phone: str | None) -> Contact | None: ...
class CrmWriter(Protocol):
    def create_contact(self, contact: Contact) -> str: ...
    def create_activity(self, contact_ref: str, activity: Activity) -> str: ...

class PortalSource(Protocol):       # ImmoScout24 etc. — unverified
    def find_listing(self, external_ref: str) -> PortalListing | None: ...   # the postcondition
    def list_leads(self, since: str) -> list[Lead]: ...
class PortalPublisher(Protocol):
    def publish_listing(self, payload: ListingPayload, files: list[FileRef]) -> str: ...
    def deactivate_listing(self, external_ref: str) -> None: ...   # named, own runbook
```

**Tables** (module branch, `immo_` prefix): `immo_owner`, `immo_property`, `immo_listing_draft`
(immutable revisions — an approval binds a `revision_hash`), `immo_document` (→ `stored_file`),
`immo_lead`, `immo_viewing`.

**Runbooks:**

| Runbook | Tier | Governed shape |
|---|---|---|
| `immo_owner_onboarding` | workflow | pre: contact not in CRM → `create_contact` → post: `find_contact` finds it; document checklist complete is a precondition of the next steps |
| `immo_draft_listing` | task | a model writes the exposé from documents and photos into `immo_listing_draft` (module-owned, not external); ends `action_required` for review |
| `immo_publish_listing` | workflow | pre: draft complete, `approved_revision_hash == current`, `find_listing` absent → approval (always) → `publish_listing` → post: `find_listing(ref)` matches the payload's fingerprint. Async feed → run ends `submitted`, a task schedules the verifying read |
| `immo_deactivate_listing`, `immo_update_price` | workflow | one named mutation each, re-read after |
| lead intake | workflow orchestrator | reads `list_leads`; each CRM activity is its own governed run |
| viewing line (optional) | conversational | its kit books viewings only through a contract with no approval step |

**One agency's workspace:** `workspace_module(immobilien, data_version 1, config {agency_name,
portals, approver_roles, exposé_tone, legal_disclaimer})`; connections `crm` → the agency's CRM,
`portal` → its portal; sections Owners (records), Listings (records), Exposé (custom), Approvals,
Runs, Settings (config); optionally one voice agent with `module_key = immobilien`. A calendar for
viewings stays inside the module — a generic calendar is lifted into the kernel only when a third
vertical needs the same one (copy twice before you abstract).

## The hardest problems, in the order they bite

1. **What a module version means.** Code exists once per process; only data can be pinned
   (ADR-0022 D6). Step 0 makes that true and is the safe first PR.
2. **The conversation kit on the live voice path** (2c, 3b). Last among the refactors, voice gate
   and shadow call on every PR.
3. **Approval that suspends without changing the engine** (6a). Resume-by-re-execution depends on
   preconditions being idempotent; `required_when` may still force an engine change — verify the
   laufwise gate API first.
4. **Writes you cannot re-read** (D9). Whether `publish_listing` can be governed at all depends on
   the customer's portal API — get those facts before step 7.
5. **Making the write seal structural** (D4). The read/write port split and the import bans land
   with the registries, or they never land.
