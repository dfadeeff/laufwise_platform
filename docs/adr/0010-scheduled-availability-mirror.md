# ADR 0010 — the occupancy mirror runs on a backend clock, not on someone's open browser tab

- **Status:** Proposed (2026-09-18)
- **Deciders:** project owner + architecture session
- **Amends:** [0009](0009-thevea-occupancy-mirror.md) — its "Deliberately not building: a schedule /
  cron (next step, owner decision 3)" is now built. Nothing else in 0009 changes: the governed unit
  is still one day, the roles are still `occupancy`/`site`, the mirror still holds no write
  capability on the practice calendar, and 0004 D7 is untouched.

## Context

0009 shipped and works. Verified against the live systems on 2026-09-17/18: after two runs, all
**688 offered slots across 43 days** agree with the practice calendar — 0 slots selling a taken
time, 0 hiding a free place, 0 unmirrored days.

Getting there exposed three things the manual trigger cannot fix, all of them structural rather
than bugs:

1. **The clock is a browser tab.** `startMirror` fires from a `useEffect` when the import job
   reports `completed` (`frontend/src/app/studio/configure/[template]/page.tsx`). Close the tab
   mid-import and the mirror never runs for that press. 0009 called this acceptable for a manual
   button and named the schedule as the fix.
2. **One press cannot cover the horizon.** `_MAX_DAYS = 62` in `app/sync/mirror.py`, and the
   website sells `BOOKING_HORIZON_MONTHS = 2`. The first run used 18.09–18.10 and left everything
   from 19.10 unmirrored — 1 slot selling a fully-booked time, 23 offering more places than exist.
   A second run with 19.10–18.11 closed it. **Two presses for one horizon, and the horizon rolls
   forward every day**, so its far edge is always the stalest part.
3. **Freshness is "as of the last press".** A phone booking taken at 10:00 is invisible to the
   website until someone presses the button. The practice's own habit — closing times by hand in
   `bookingConfig.ts`, one commit per change — was what 0009 set out to replace; a button that must
   be remembered replaces it only halfway.

A fourth thing is visible in the run reports and matters for the design: **the practice calendar is
not reliably fast**. Both runs produced transport failures under manual load —
`The read operation timed out` and `[Errno 104] Connection reset by peer`. Whatever cadence this
ADR picks has to be gentle, and a failure has to be a normal, retried event rather than an alarm.

### What already exists and must not be rebuilt

- `run_mirror(session, runtime, instance, window, on_progress)` (`app/sync/mirror.py`) already
  takes the window as an **argument**, not from `param_values`. A caller with a different clock
  needs no change here.
- `spawn_import_job` / `execute_import_job` (`app/sync/jobs.py`) already run a job off the request,
  in a thread with its own event loop and DB engine, dispatching by template name through
  `_ORCHESTRATORS`.
- `repo.running_import_job_for_instance` is already the concurrency guard, and it lives in the DB —
  so it works across processes, not just across requests.
- Railway already builds this image and runs `alembic upgrade head && python scripts/seed.py` as
  `preDeployCommand` (`backend/railway.json`).

The gap is therefore narrow: **a clock, a rolling window, a record of which instances are armed,
and a way to not wedge on an orphaned job.** Nothing else.

## Success criterion

With no browser open anywhere:

1. For every day the website offers, the occupancy it shows equals the practice calendar's, with a
   staleness bound of **≤ 20 minutes for today … +7 days** and **≤ 24 hours for the rest of the
   booking horizon**.
2. The horizon's far edge is covered as it rolls forward — no day ever reaches the website's
   booking window without having been mirrored at least once.
3. **A platform restart costs at most one tick.** No timer lives in memory.
4. Two ticks never run the same instance concurrently, and an orphaned job cannot wedge the
   schedule permanently.
5. Every scheduled fire is recorded as a job with the same report shape the button produces, and is
   **distinguishable** from a manual press.
6. The practice calendar sees **fewer than ~1000 day-reads per day**, and never a burst during
   treatment hours that a manual run would not also have caused.
7. Arming and disarming a schedule is a property of the instance, not a code change.

## Decision

### D1 — The clock is a separate Railway service running the same image, not a timer in the API

`backend/railway.json` gains nothing; a **second Railway service** is created from the same
Dockerfile with a cron schedule and `startCommand: python -m app.sync.scheduler`. It runs, does its
work, and exits.

Rejected alternatives, each for a reason in this repo rather than a general preference:

- **A timer inside the FastAPI process** (APScheduler, or an asyncio task on startup). The API tier
  "never runs the agent loop inline" (PLATFORM_PLAN §6.2); a second replica would double-fire; and
  a restart silently loses the timer, which fails criterion 3 and the workflow-procedures rule that
  parked state is persisted, never in-memory.
- **Temporal.** PLATFORM_PLAN §6 is explicit that heavy infrastructure is adopted "only when a
  workload demands it, never on day one". One periodic sweep over ≤ 70 days does not demand a
  workflow engine. The `Engine` seam is what makes this reversible later.
- **A cron on the practice's website** (`vercel.json` already has one for reminders). That points
  the wrong way: the site is a *destination* of this process, and making it the platform's clock
  couples a customer surface to platform orchestration.

The cron container **does the work in-process** — it imports `run_mirror` and talks to the same
database — rather than calling the HTTP API. This deliberately avoids inventing a machine
credential for an API whose auth is Clerk-organization-shaped. The write path it uses is the same
governed one; nothing bypasses the contract.

### D2 — Two cadences, because one would be either stale or abusive

The scheduler is invoked with a tier, and the tier decides the window:

| Tier | Railway cron | Window | Day-reads/day |
|---|---|---|---|
| `near` | `*/20 * * * *` | today … +7 | 72 ticks × 8 = 576 |
| `horizon` | `0 3 * * *` (03:00 Berlin) | today … +70 | 70 |

~650 day-reads per day against the practice calendar, versus ~6000 if a single 20-minute tick swept
the whole horizon. The nightly sweep lands when the practice is closed, so the heavy pass never
competes with treatment-hour use of thevea.

This is **pure orchestration** and needs no template change: 0009 D3 already put "which days" above
the engine and "may this day be written" inside it. The near tier is what makes a phone booking
visible quickly; the horizon tier is what guarantees criterion 2 and repairs anything the near tier
failed on.

`_MAX_DAYS` rises from 62 to **70** in `app/sync/mirror.py`. At 62 the cap and the website's own
2-month horizon are the same number, so the last day or two of the horizon can fall outside a
sweep — exactly the edge that is hardest to notice is wrong.

### D3 — A schedule is one nullable column on the instance

`agent_instance` gains `schedule: str | None` (`NULL` = manual only; `"mirror"` = armed for the
tiers in D2). The scheduler selects deployed instances with a non-null `schedule`; an instance that
is `paused` is not fired, so **pausing already disarms** and no second flag is invented.

Rejected: a cron expression per instance. That is configurability without a demand (CLAUDE.md §III)
— it would add an expression parser, a timezone question, and a way to write a schedule that
hammers the practice calendar, to serve a choice nobody has asked to make. A named schedule is a
value the platform understands and can reason about; a cron string is a value it can only obey.

### D4 — The window is computed at fire time and passed as an argument

The scheduler builds `{"from": today, "to": today + N}` in **Europe/Berlin** and hands it to
`run_mirror`. `param_values.window_from/window_to` keep their meaning for the manual button and are
**not** read by the scheduler.

This is the whole reason the horizon edge stops rotting: a stored window is a fact about the day it
was typed, and the booking horizon is a fact about today.

### D5 — An orphaned job is reclaimed, not worked around

`app/sync/jobs.py` already documents that "a process restart mid-run orphans the job as `running`".
Combined with the `running_import_job_for_instance` guard, one orphan would block **every future
tick forever** — a leak that would be invisible until someone noticed the website going stale.

Before consulting the guard, the scheduler marks any `import_job` that is still `running` with
`updated_at` older than **30 minutes** as `interrupted` (a new value of `ImportJob.status`, which is
a *lifecycle* column — distinct from `Run.status`, which is a governance *outcome*; the
workflow-procedures skill is explicit that collapsing the two is wrong).

30 minutes is comfortably longer than the nightly 70-day sweep and far shorter than the interval at
which staleness would matter.

### D6 — A scheduled fire is labelled

`import_job` gains `trigger: str` (`manual` | `schedule`, default `manual`). Without it, the
operator cannot tell a run they started from one the clock started, and "did the schedule actually
fire?" becomes unanswerable from the record — which is the same failure the Belegung page was built
to end.

One Alembic migration carries D3, D5's new status value (no DDL — it is a string column) and D6.

## Consequences

- **New:** a Railway cron service; `app/sync/scheduler.py`; `AgentInstance.schedule`;
  `ImportJob.trigger`; `ImportJob.status = interrupted`; one migration. `_MAX_DAYS` 62 → 70.
- **No engine change, no template change, no connector change.** `availability_mirror.yaml` stays
  at v1 and every deployed instance keeps working — the schedule is a *caller*, and 0009 D3 already
  made the caller replaceable.
- **The Studio button stays.** It is now the "do it right now" path, not the only path, and it is
  also the test path — and per the workflow-procedures rule, **a test run is a real run**: it reads
  and writes the same production systems. The UI already says so; keep it saying so.
- **The practice calendar gets steady background load** it did not have before: ~650 day-reads
  daily, concentrated outside treatment hours for the heavy pass. Transport failures stop being
  events someone reads and become normal, retried occurrences — the next tick repairs them, which
  is why no retry logic is added here.
- **Freshness becomes a number that can be stated**: ≤ 20 minutes near, ≤ 24 hours far. Today it is
  "whenever someone last pressed the button", which cannot be stated at all.
- **This is the first trigger on the platform.** It is deliberately the narrowest possible one —
  named, single-purpose, no payload. The general trigger seam (webhook, arbitrary schedule) is the
  workflow-procedures roadmap's item 5 and should be designed when a second process needs it, not
  extrapolated from this one (CLAUDE.md §X, the Wrong Abstraction).

## Deliberately not building

- **A schedule for `calendar_import`.** It *writes into a medical record system*. Append-only makes
  a duplicate impossible, but a write nobody is watching is a different risk class than a read, and
  the 2026-09-17 run already produced a `state_unavailable` on a verification read where the
  appointment had in fact landed. Automating that deserves its own decision, and the mirror's
  occupancy maths is already correct for a website booking that has not yet reached thevea
  (`occupiedAtSlot` counts it once, from the site side).
- **Retries, backoff, alerting.** The next tick is the retry. A failure that outlives several ticks
  is visible on `/api/admin/belegung` as a day whose stamp has stopped moving.
- **A generic trigger seam** (webhook, per-instance cron, callbacks) — see D3 and Consequences.
- **Temporal, a queue, a broker.** PLATFORM_PLAN §6, staged infra.
- **Reading the practice calendar in one range query instead of per day.** It would cut reads
  further, but the governed unit is one day (0009 D1) and the saving is not yet needed.

## Open questions

- **Does Railway's cron give the granularity and the reliability assumed here?** `*/20` needs a real
  cron service, and a missed fire must be acceptable (it is — the next tick repairs). Confirm before
  committing to the 20-minute figure; if only hourly is practical, the near tier's bound becomes
  ≤ 60 minutes and criterion 1 should be restated rather than quietly missed.
- **Which instance is armed, and who arms it?** D3 adds the column but not the UI. Setting it by
  hand once is fine for one practice; a Studio toggle is the obvious follow-up and should wait until
  someone other than the owner needs it.
- **`preDeployCommand` already runs `alembic upgrade head && python scripts/seed.py`**, while
  DEPLOY.md §"the app never seeds on boot" reads as though seeding is manual. The two should be
  reconciled — this ADR assumes the railway.json behaviour is the truth, and its migration runs
  automatically on deploy.
- **Timezone of the nightly sweep.** Railway cron is UTC; `0 3 * * *` UTC is 04:00 or 05:00 Berlin
  depending on the season. Either accept the drift (the practice is closed at both) or shift the
  expression twice a year — the former, unless a reason appears.
