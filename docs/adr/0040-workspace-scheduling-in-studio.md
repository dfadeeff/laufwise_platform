# ADR-0040 — A workspace schedules its agents in the Studio; the platform owns the one clock

Status: proposed · 4 November 2026 · not built

Builds on ADR-0003 (one Clerk organization = one tenant), ADR-0006 (`Trigger` is operational),
ADR-0016 (agency workspaces), ADR-0030/0031 (workflow agents, chosen by kind), ADR-0035
(approvals) and ADR-0039 (procedures with one trigger, guardrails the platform enforces).
If accepted, it supersedes ADR-0013 (proposed, never built) and ADR-0010 D1 and D3: the
per-process Railway cron services, and the rule that a schedule can only have a fixed name. ADR-0010 D2's
cadences and D4–D6 stand. The status lines of earlier ADRs are left unchanged until this one is
accepted.

## Context

A workspace, such as Hanna's organization, wants its agents to work on a schedule: "weekdays at
09:00", "every two hours between 08:00 and 18:00". It must set that up, see it working, and pause
it, in the Studio, by itself.

**What exists today:**

1. **The mirror's clock is two Railway cron services** (`backend/railway.scheduler-near.json`,
   `railway.scheduler-horizon.json`, ADR-0010 D1; DEPLOY.md §1b). The cadence lives in
   Railway's dashboard, and a new cadence would mean a new Railway service that only an operator
   can create.
2. **Arming is a named value on the instance.** `agent_instance.schedule = "mirror"` and
   `PUT /instances/{id}/schedule` exist for the two mirror templates only (ADR-0010 D3). Arming one instance
   disarms the tenant's other instances armed for that name, and the user cannot choose a time.
   `app/sync/scheduler.py` does the work through the governed import path. This is useful
   automation for one domain, not generic scheduling.
3. **ADR-0013 proposed `AgentSchedule`**, an operational record bound to the agent (the shape of
   `VoiceChannel`). It kept named-only schedules on the grounds that nobody had asked for a
   choice of time. It was never built. That demand now exists: workspaces build their own agents
   (ADR-0039), and an agent that only a person pressing a button can start is half an agent.
4. **Three housekeeping clocks run inside the API process** as asyncio loops started in
   `lifespan` (`app/main.py`): transcript retention, website prices (ADR-0032), and wait expiry
   (ADR-0039 step 3b). They are platform chores, not user schedules.
5. **Two agent models exist side by side:** `StudioAgent`/`AgentInstance`, which run runbooks, and
   `WorkflowAgent`/`DefinitionVersion`, which run case workflows. Scheduling must not pretend that
   every agent already has a `published_instance_id`.

### Wonderful reference (source read locally in `work_projects/wonderful/repos/wonderful`)

- **One engine for every tenant.** `common/scheduler` is a library embedded in each service, and
  Postgres is the only thing it coordinates through: `FOR UPDATE SKIP LOCKED`, a lease
  (`locked_by`/`locked_until`) with a 10 s heartbeat and a 5 min expiry, updates fenced on
  `locked_by = nodeID`, fair claims per tenant, and a cluster-wide concurrency semaphore. Its
  README rejects Kubernetes CronJobs and Temporal: CronJobs would need per-tenant manifests and
  RBAC.
- **The user's entity is `AgentTrigger`** (`common/models/task.go`), scoped to tenant + agent. It
  has `trigger_type=schedule`, `task_context` (the input), a structured `schedule_config`
  (`every`/`unit`/`at_time`/`days_of_week`/`day_of_month`) or a `cron_expression`, a `timezone`,
  `run_once_at`, `next_run_at` and `owner_user_id`. It is exposed as
  `GET|POST|PATCH|DELETE /agents/:id/triggers`, behind `agents.view` / `agents.edit`, and changes
  are audited.
- **Delivery:** every occurrence has a deterministic id derived from (trigger, run_at) and its
  own lock. The claim is committed after delivery, and a refresh sweep repairs broken chains.
  Missed fires collapse into one, with no backfill. A recurring cron function gets one attempt,
  because "the next tick is the retry". Before every fire, the owner's membership and permission
  are checked again, and if either is gone the trigger is disabled.
- **Actions schedules** (`wonderful-actions/workflow/schedule.go`) take a bounded five-field cron
  string, enforce a minimum interval, and are fixed to UTC.
- **Campaigns** (the outbound dialer) are separate. Each campaign has one interval job, plus call
  windows and attempt limits, and pausing a campaign disables its job.

**Where we deliberately differ.** We start with presets, not a cron string, and in local time,
not UTC. A scheduled run that reaches an approval step **waits for a person**. Wonderful's
campaign demo moves on when the model writes "APPROVAL_ELIGIBLE: YES"; here no clock and no model
output opens a gate (ADR-0039).

## Success criterion

1. An administrator of Hanna's workspace picks a published, schedulable agent, gives its input,
   sets "weekdays at 09:00 Europe/Berlin", previews the next runs, enables it, and later pauses
   it, all in the Studio. It runs with no browser open, and survives worker restarts and
   deploys.
2. Another workspace does the same independently. **Adding the hundredth schedule of the
   hundredth workspace changes nothing in the hosting setup**: no code, no deploy, no Railway.
3. Each due time produces at most one occurrence. A crash never loses an occurrence silently and
   never duplicates a consequential effect silently.
4. A scheduled run is an ordinary governed run: same preconditions, allowlist, approvals,
   postconditions, checks and trace. A schedule grants no permission and supplies no approval.
5. A workspace sees, edits and fires only its own schedules, agents and connections. An unowned
   id is a 404.
6. The agent's page answers "is it scheduled, when does it next run, did the last run work?"
   from the record. A skipped or interrupted fire is visible there.

## Decision

### D1 — Scheduling is a kernel capability; its configuration belongs to the workspace

The platform owns timing, validation, durable dispatch, recovery, permissions and audit. The
workspace (`tenant_id`) owns its schedules, the agents they target, their input, their timezone
and whether each one is on. There is no second workspace hierarchy, and no worker or service per
customer.

Ownership is checked per workspace because the workspace is already the unit of tenancy, roles
and isolation (ADR-0003, ADR-0016, ADR-0039). But a schedule always targets **one agent**: a clock
with nothing to run means nothing. A workspace may have many schedules over many agents, and
enabling one never disarms another. That ends today's "arming one disarms the rest".

Every route resolves the signed-in workspace and checks that the schedule, agent, version, input
references and connections all belong to it. The dispatcher's cross-workspace query is
privileged only to *discover* due work. Every execution carries the persisted workspace identity
and re-validates the target before it starts.

### D2 — Railway is private infrastructure, never the customer's scheduling interface

**Customers do not receive or require Railway access, credentials, project membership, service
IDs, or dashboard links to configure or operate scheduled work.** The Studio calls the laufwise
API, never the Railway API. Neither user input nor the builder (ADR-0039 D6) can configure host
commands, images, environment variables or infrastructure cron.

The host's whole contract is: **keep one command running**, `python -m app.schedules.worker`,
from the same image. That command never changes when a workspace adds a schedule. The
application database holds every schedule, and application code computes when each one is due.
Moving to another host changes no API, no record and no Studio screen. Operators also need no
Railway access to support a customer's schedule.

Rejected, with reasons grounded in this repo:

- **Railway cron per schedule or per workspace.** This is the infrastructure-per-customer this
  ADR exists to end.
- **One Railway cron firing a tick every minute.** Correct, because the claim is atomic, but
  Railway skips later executions while one is still running (DEPLOY.md §1b). One hung tick would
  stop every workspace's schedules.
- **The API's `lifespan`, like prices and waits.** It would be safe with replicas, but runs
  started by the clock would compete with live calls during deploy draining (ADR-0023, up to
  11 min). A separate worker can be redeployed without touching calls.
- **Temporal.** PLATFORM_PLAN §6 adopts heavy infrastructure only when a workload demands it.
  Wonderful runs this on Postgres alone, and the `Engine` seam keeps that choice reversible.

The housekeeping loops stay where they are. Moving them onto the worker is a later, optional
cleanup.

### D3 — `AgentSchedule` targets a published entrypoint, through a dispatch adapter per runtime

```python
class AgentSchedule(Base):            # operational, outside the versioned definition
    __tablename__ = "agent_schedule"
    id, tenant_id                     # every query scoped by tenant_id
    target_kind: str                  # "workflow" | "runbook" — names the dispatch adapter
    target_id: uuid                   # WorkflowAgent.id | StudioAgent.id, same tenant (FK-checked)
    target_version_id: uuid           # the published version it runs — explicit, see below
    operation: str                    # the entrypoint, e.g. "create_case" | "import"
    name: str                         # what the user calls it
    rule: dict                        # D4 — structured, never a cron string
    timezone: str                     # IANA; defaults to the workspace's, copied at creation
    input: dict                       # validated against the target's declared input schema
    enabled: bool
    next_run_at: datetime | None      # UTC; NULL while paused
    revision: int                     # bumped on every configuration change
    created_by, updated_by: str       # Clerk user ids
    created_at, updated_at
```

- **A dispatch adapter per runtime** (`app/schedules/targets.py`): one for runbooks, which calls
  `execute_import_job`, the entry the Studio button uses, and one for case workflows, which uses
  the case-run entry. The clock knows no domain. A target is selectable only if its definition
  declares a scheduled entrypoint and an input schema. ADR-0039 D3's "one trigger" becomes
  `trigger: manual | schedule`. The engine does not change.
- **Case workflows: an occurrence creates a case** from the schedule's input, titled by the
  schedule's name and due date. There is no implicit "run all cases" and no input invented to
  fill a gap. A procedure whose input needs documents that a person brings cannot be scheduled,
  and the Studio says so. "Select existing eligible cases" is a later operation.
- **Explicit version.** Publishing a new version does **not** move the schedule silently. The
  Studio shows "version 4 is published; this schedule runs version 3", validates the input
  against version 4, and the admin updates the schedule. Drafts, disabled targets and
  unavailable targets are refused. *(A schedule that follows every publish would let an edit in
  the builder change what runs unattended at 03:00.)*
- **Operational, not versioned** (ADR-0006, ADR-0013 D4). Enabling or pausing needs no publish
  and no publish gate. Arming changes **when** an agent runs, never **what it may do**.
- **Conversational agents are not schedulable.** A schedule does not turn an inbound receptionist
  into an outbound caller. Scheduled outbound calls are a campaign (call windows, consent,
  attempt limits) and get their own ADR on the task tier. A future runtime becomes selectable
  only once it registers its own entrypoint.

### D4 — The Studio offers structured timing in local time

**One generic grid: every time is on a 30-minute slot** (`:00` or `:30`, local time). This is
the scheduler's resolution and its fastest cadence. Nothing runs more often than every 30
minutes, and every schedule is expressed on the same grid, so "how much will run at 09:00?" is
always answerable.

**The typical use is a few fixed times a day, around three.** So the main preset is **at these
times, on these days**: for example 08:00, 13:00 and 18:00 on weekdays, stored as
`{"kind": "times", "days": [1,2,3,4,5], "at": ["08:00", "13:00", "18:00"]}`. Daily or weekly at
one time is the same preset with one entry. The second preset is **every 30 minutes or every N
hours**, optionally only between two local times on chosen days. Rules are validated onto the
grid and turned into `next_run_at` by one pure function. Monthly rules, one-off runs and a raw cron field are deferred until a workspace
asks. The storage does not change when they come.

**Rejected: a cron field.** A cron string is something the platform can only obey, not reason
about (the argument of ADR-0010 D3, which still holds). Presets can be shown in German ("werktags
um 09:00"), previewed as the next runs, and checked against D6's limits on the server.

**Time:** due times are stored in UTC, with the IANA timezone stored beside them. Both presets
follow local wall time, intervals included: "every 2 hours from 08:00" means 08:00, 10:00, 12:00
on the clock, so every time stays on the grid and can be previewed. A local time that doesn't
exist on a DST change is skipped, and a local time that repeats fires once, at its first
occurrence. So once a year, on the night clocks go back, an interval has one gap an hour longer
than usual (an "every 30 minutes" schedule waits 90 minutes once). The preview shows this. Changing the workspace's default timezone never changes an existing
schedule.

### D5 — Occurrences are persisted and claimed atomically; delivery is at-least-once, effects are not replayed blindly

```python
class ScheduleOccurrence(Base):       # append-only history, and the work queue
    __tablename__ = "schedule_occurrence"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_for"),)
    id, tenant_id, schedule_id
    scheduled_for: datetime           # the due time, not when it ran
    origin: str                       # schedule | run_now
    schedule_revision: int            # snapshot of what was configured
    target_version_id, input          # snapshot
    status: str                       # queued | claimed | started | succeeded | failed
                                      # | skipped_busy | skipped_missed | blocked | cancelled
    locked_by, locked_until           # lease
    attempt: int
    run_ref: str | None               # the CaseRun / ImportJob it started
    error: str
```

**Enqueue (one transaction).** The worker claims due schedules with `FOR UPDATE SKIP LOCKED`,
taking workspaces in turn so none is starved. It inserts the occurrence, unique on
`(schedule_id, scheduled_for)`, with its snapshot, and advances `next_run_at`, all in one commit.
A crash cannot advance the cursor without enqueuing the work, and a second worker's insert
fails. A check-then-insert without the lock and the constraint would not be enough.

**Execute (lease).** A worker claims a `queued` occurrence with a bounded lease and a heartbeat.
Every update is fenced on `locked_by`, so a stale worker can neither mark an occurrence complete
nor start a new effect. Recovery reclaims expired leases. Before it starts, the worker checks
three things:

- the schedule is still enabled and the target still eligible;
- the schedule's `created_by` is still an admin of the workspace, and if not, the schedule is
  paused and the occurrence `failed` with the reason (Wonderful's re-check);
- no other run of this target is active.

**Honest semantics.** Dispatch is **at-least-once**. The occurrence id is the deduplication key
across retries, passed to the run, and to a provider as an idempotency key where it supports one.
If a run was interrupted mid-effect and the outcome is ambiguous, the occurrence goes to
`blocked` for verification. It is never blindly replayed. The governed postcondition, which
re-reads real state, is what decides; a unique row is not a promise of exactly-once effects.

**Overlap, misses, retries:**

- **Overlap:** the default is *skip while the target already has active scheduled work*,
  enforced by an atomic guard shared across all of the target's schedules, plus the existing
  guard per connection (`running_import_job_for_instance`). The skip is recorded as
  `skipped_busy`.
- **After downtime:** missed occurrences coalesce into one current run, and the missed range is
  recorded as `skipped_missed`. An unbounded backlog is never replayed.
- **Retries:** transport failures get bounded backoff with a timeout. A recurring occurrence gets
  one attempt, because the next occurrence is the retry. A blocked approval or a governance
  refusal is not a transport failure and is never retried automatically.
- **Pause** stops every occurrence that hasn't started, including queued ones, because the worker
  re-checks after locking. The boundary is the execution-start transaction: a run already past it
  stays active and is shown. Pause never undoes an effect. Resume schedules from the next future
  time.

### D6 — Limits the platform enforces

| Limit | Where | Why |
|---|---|---|
| Minimum interval | rule validator: **30 min**, the grid (D4) | a schedule must not become a load test of a practice system |
| Active schedules per workspace | on save (provisional: 20) | a runaway configuration is a support case, not a bill |
| Concurrent occurrences per workspace | worker claim | one workspace cannot consume the queue |
| One active scheduled run per target | D5 guard | two schedules on one calendar never read it twice at once |
| Execution time | lease + run timeout | a hung run cannot hold a target forever |

The numbers are platform constants first (CLAUDE.md §III) and are set against the first real
workload before the feature is switched on. They become workspace settings only when a workspace
needs a different value. The mirror keeps its cadence protections, and its near and horizon
windows stay in its adapter, not in the generic timing code.

### D7 — The clock changes when work starts, never its governance

Every occurrence dispatches through its target's existing governed service. Runbooks keep their
preconditions, allowlists, approvals, postconditions and traces. Case workflows keep input
confirmation, the mandatory check, resume pinned to a version, and human decision gates. **A
scheduled run that reaches an approval step stops there and waits for a person** (ADR-0035).
Nothing leaves the workspace because a clock fired. A recurring trigger is a different thing from
a wait's timer (ADR-0039 step 3b), which only ends a wait and never starts work.

"Run now" goes through the same dispatch with `origin = run_now`, respects the overlap guard,
and the Studio states that a live run performs real actions.

### D8 — Studio, API, roles

- **On the agent's page, under Connect** (ADR-0013 D5's placement: a phone number is how a caller
  reaches the agent, a schedule is how time does). The section lists the agent's schedules, with:
  - enabled or paused, shown separately from health;
  - the version it runs, and whether a newer one is published;
  - the next five times;
  - the last outcome and failure detail;
  - the history of occurrences, each linking to its run.
- **A workspace "Zeitpläne" list:** every schedule, sorted by next run, with pause. This is where
  "what will happen tomorrow?" is answered.
- **Layers:** HTTP only in `app/api/v1/schedules.py`; timing, dispatch and targets in
  `app/schedules/` (no FastAPI imports); queries in `app/db/repo.py`; wire contracts in
  `app/schemas/schedule.py`. Routes, all tenant-scoped:
  - `GET/POST /schedules`
  - `GET/PATCH/DELETE /schedules/{id}`
  - `POST /schedules/{id}/pause` and `/resume`
  - `POST /schedules/{id}/run-now`
  - `GET /schedules/{id}/occurrences`
  - `POST /schedules/preview` (the next N times for a rule)
- **Roles:** the Clerk role `org:admin` creates, edits and deletes schedules. Any member may
  pause, because stopping is always safe. Every member sees the history. Every configuration
  change records who made it and when. `DELETE` keeps the occurrence history.

### D9 — Migrate the mirror without running it twice; prove the guarantees before rollout

1. Ship the schema and the worker with Studio activation switched off.
2. Migrate: give each deployment armed for `mirror` a stable `StudioAgent` identity (ADR-0013
   D1), and create its `AgentSchedule` rows: `near` every 30 min (from 20; on the generic grid,
   with no exception for one domain) and `horizon` daily at 03:00,
   Europe/Berlin, both through the mirror adapter, which keeps the window computed at fire time
   (ADR-0010 D4).
3. Cut over **one target at a time, atomically**, so exactly one clock owns it. Roll back by
   handing ownership back, explicitly. The old and new clocks are never both on for the same
   armed deployment.
4. Only after parity is verified, remove `agent_instance.schedule`,
   `PUT /instances/{id}/schedule`, the two Railway cron services and DEPLOY.md §1b. A single
   DEPLOY.md line for the worker replaces them.

**Release evidence required** (tests, not claims):

- **Isolation:** another workspace is refused, permission checks hold, and sibling schedules stay
  independent.
- **Validation:** drafts, wrong versions and invalid input are refused.
- **Time:** next-run calculation, including both DST transitions.
- **Concurrency:** two concurrent workers; crashes around the enqueue and commit; an expired lease
  and a stale worker fenced out; dispatch deduplicated; an ambiguous effect blocked; overlap
  guarded across schedules; pause racing a claim.
- **Recovery and fairness:** missed occurrences recovered within bounds; workspaces served
  fairly.
- **Unchanged behaviour:** governance unchanged; the mirror migrated with no duplicate run.
- **End to end:** a run from the Studio with the browser closed.

**Operations:** monitor scheduling lag, the worker's heartbeat, the oldest queued occurrence and
the failure rate, and alert operators when scheduling stops making progress.

## Not building

- **Cron strings, monthly rules and one-off runs** (D4).
- **Event and webhook triggers.** They are the next trigger and will sit beside this table.
- **Scheduled outbound calls / campaigns** (D3).
- **Temporal** (D2).
- **Limits as per-workspace settings** (D6).
- **Holiday calendars.**
- **Moving the housekeeping loops** (D2).

## Consequences

- Hanna's organization becomes configuration in a generic multi-workspace product, not customer
  code.
- **New:**
  - the tables `AgentSchedule` and `ScheduleOccurrence`;
  - the module `app/schedules/` (rules, worker, targets);
  - the routes `app/api/v1/schedules.py` and the contracts `app/schemas/schedule.py`;
  - a `trigger` field on `CaseRun`;
  - one worker in the host, set up once;
  - the Studio sections.
- **Removed after cutover:** `agent_instance.schedule`, the instance schedule route, and two
  Railway cron services.
- **No engine change.** The clock decides when; the contract decides what (ADR-0010, ADR-0013
  D4).
- **ADR-0013** is superseded before it was built. Kept: D1 (agent identity), D2 (operational
  record), D4 (arming is not a publish) and D5 (under Connect). Reversed: D3 (named schedules
  only), replaced by D4 presets bounded by D6.
- A workspace can now raise the load on its practice systems; D6 bounds it.
- New persisted state needs migrations and monitoring. A timer plus a form does not meet the
  success criterion.
- No implementation, live workspace change or infrastructure change comes with this ADR, and it
  makes no claim that the platform is production-ready.

## Open questions

- **The first example for Hanna's workspace.** Which published workflow, and which scheduled
  operation, is the first acceptance case? Its input decides the first dispatch adapter.
- **The numbers.** The workspace quota and the execution limit must be set against that
  workload before the feature is switched on. The interval is decided: 30 minutes.
- **Mirror freshness.** Moving the mirror's `near` tier from 20 to 30 minutes loosens ADR-0010's
  freshness bound for today … +7 days from ≤ 20 to ≤ 30 min. The default is to accept that and
  keep one grid; the alternative is a platform-owned exception for the mirror.
- **Failure notifications.** A failed scheduled run that nobody sees is the Belegung problem
  again. Probably a mail to the schedule's creator after N consecutive failures (ADR-0024).
- **Opening hours.** "Only during opening times" could read the workspace's hours instead of
  repeating them in the rule, once hours are a workspace fact.
- **Agency workspaces (ADR-0016).** Default: the Zeitpläne list shows one workspace at a time, as
  everywhere else.
