# ADR 0013 — A schedule is something you add to an agent, not a column you edit by hand

- **Status:** Proposed (2026-09-20) — supersedes [0010](0010-scheduled-availability-mirror.md) D3
  (`agent_instance.schedule`) and closes its open question *"Which instance is armed, and who arms
  it?"*. Lands the `Trigger` record [0006](0006-agent-taxonomy.md) committed to, for the workflow
  tier, ahead of the task tier.

## Success criterion

A practice owner can answer **"is this agent on a schedule, and when did it last run?"** from the
agent's own page in the Studio, and can arm or pause it there — without a database session, and
without choosing between rows that look identical. A second tenant costs nothing but their own
setup.

## Context

ADR-0010 built the clock and stopped one step short of the controls. Its D3 put the schedule on
the instance as a nullable column, and its own open questions (line 215) said why that was enough
at the time:

> Setting it by hand once is fine for one practice; a Studio toggle is the obvious follow-up and
> should wait until someone other than the owner needs it.

Someone other than the owner now needs it. Three things make the hand-edited column untenable
rather than merely unfinished:

1. **A deployed workflow has no identity.** `deploy_instance` (`app/api/v1/instances.py`) never
   sets `agent_id`, so every `availability_mirror` deployment is an orphan `AgentInstance`. Press
   the button twice and you have two rows that differ only by `created_at`. There is nothing to
   update, so the only thing the button can do is insert — which is the duplicate-instance bug,
   and it is a *missing entity*, not a missing `ON CONFLICT`.
2. **Nothing reads the column back.** `schedule` is written by hand and displayed nowhere, so
   "is this practice mirroring?" is a question only SQL can answer, and "who armed it, and when?"
   has no answer at all.
3. **The clock is deliberately cross-tenant.** `repo.scheduled_instances` (`app/db/repo.py`)
   selects every armed deployed instance across tenants — correct, and documented as correct: *"the
   clock is not a request and acts for nobody"*. But it means the only thing standing between one
   practice and another practice's calendar is the operator picking the right row out of a list.

The shape this should take was already decided. ADR-0006's consequences name

> `Trigger` as an **operational** record, deliberately *outside* the versioned template: ops must
> be able to disable an entrypoint without a release, the same reason `Connection` is bound
> per-instance and not authored in the contract.

and the platform already has one working example of exactly that record: `VoiceChannel`. A phone
number is an entrypoint bound to an agent, pinning the instance that answers, with `active` to
pause it without forgetting who owns it. **A clock is the same thing as a phone number**: another
way the outside world reaches an agent. It should be the same shape, and it is not one today only
because the clock was built before the agent workspace existed.

## Decision

### D1 — A workflow deployment gets a `StudioAgent`, exactly as a voice agent has one

Deploying a workflow from the Studio creates-or-reuses a `StudioAgent` for that tenant and pins
the new instance to it, the way publishing a voice agent does. The agent is the stable identity;
the instance is the pinned, immutable deployment.

This is the fix for duplicate instances, and it fixes it at the cause. Today the button inserts
because there is nothing to update. With an agent in front of it, a redeploy is *a new revision of
a known agent* — the same relationship `StudioAgent.published_instance_id` already expresses for
voice. Old instances stay, immutable, as history; exactly one is current.

It also makes the design proposal's own supersession note true in the model rather than only in
the navigation: *"Workflows are a kind of agent, listed on the Agents page beside voice
receptionists"* (`docs/design/STUDIO_REFACTOR_PROPOSAL.md`, 20 September 2026). The Studio already
lists them together; this makes them the same kind of thing underneath.

**Rejected: making the deploy endpoint idempotent on `(tenant, template, connections)`.** It would
stop the row count growing without giving the schedule anything to attach to, and it would make
"the current deployment" an emergent property of a `WHERE` clause instead of a fact on a record.
The duplicate rows are the symptom; the missing identity is the disease.

### D2 — `AgentSchedule` is an operational entity bound to the agent, not a column on the instance

```python
class AgentSchedule(Base):
    """A clock pointed at an agent. The same shape as VoiceChannel, for the same reason:
    an entrypoint is operational, pins a published instance, and pauses without being forgotten."""
    __tablename__ = "agent_schedule"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_agent_schedule"),)

    id:          uuid    # pk
    tenant_id:   uuid    # -> tenant
    agent_id:    uuid    # -> studio_agent
    instance_id: uuid    # -> agent_instance, the revision the clock runs
    name:        str     # the named schedule, e.g. "mirror"
    active:      bool    # pause without forgetting who owns it
    created_at:  datetime
```

`agent_instance.schedule` is dropped. **ADR-0010 D3 is superseded, not overturned** — its actual
decision was *"a named schedule, not a cron expression"*, and that survives intact (D3 below). What
changes is only where the arming lives, and the column was always the placeholder: D3 says "adds
the column but not the UI" in the same breath.

`UniqueConstraint(agent_id, name)` is the invariant, enforced by the schema rather than by the
operator's care. Note what it is deliberately **not**: unique per *tenant*. Two agents in one
practice, mirroring two different destinations, is a legitimate configuration and must stay
representable. The guard against two clocks reading one calendar is the connection binding plus the
existing in-database concurrency guard (`running_import_job_for_instance`), not a uniqueness rule
that would forbid a shape the platform should support.

### D3 — The schedule stays *named*; no cron expression per agent

ADR-0010 D3's rejection stands, and this ADR does not reopen it:

> a cron expression per instance ... is configurability without a demand (CLAUDE.md §III) — it
> would add an expression parser, a timezone question, and a way to write a schedule that hammers
> the practice calendar, to serve a choice nobody has asked to make. A named schedule is a value
> the platform understands and can reason about; a cron string is a value it can only obey.

`name` holds the named schedule (`"mirror"` today, matching `scheduler.SCHEDULE`). The *cadence*
belongs to the scheduled processes, not to the row: `near` and `horizon` are two tiers of one
intent, and an agent armed for `mirror` is picked up by both. So the Studio offers **one thing you
can add**, described by what it does — "Keep the website in sync" — not a cron field. One option
because one exists; the field is a catalogue key, and the catalogue grows when a second schedule is
built.

### D4 — Arming is an operational action, not a publish

`POST /agents/{id}/schedules` and `DELETE`/pause, tenant-scoped like every other agent route. It
does **not** go through the publish gate and does not create a revision, for the reason ADR-0006
gave: an entrypoint must be disable-able without a release. This mirrors `activate`/`pause` for
`VoiceChannel` precisely, and it means pausing a runaway clock is one click rather than a deploy.

The contract is untouched by this: arming changes *when* the runbook runs, never *what it may do*.
Every fire still goes through `execute_import_job` → precondition → allowlist → execute →
postcondition → trace. The clock decides when; the contract decides what. That separation is
ADR-0010's and it is the reason this can be an operational toggle at all.

**Rejected: an approval gate on arming.** It is reversible in one click, it writes nothing by
itself, and `ImportJob.trigger = "schedule"` already answers "did it fire" from the record. A gate
here would be ceremony, and CLAUDE.md §XIII is clear that a gate which is not load-bearing is
theatre.

### D5 — The Studio surfaces it under **Connect**, beside Phone & handoff

The agent workspace groups its sections Build / Connect / Verify. `Connect` is already "how the
world reaches this agent" — today that is one entry, Phone & handoff. A schedule is the second
entry, and it reads correctly there without any renaming: a phone number is how a patient reaches
the agent; a clock is how time does.

The section shows the named schedule, whether it is active, which revision it runs, and the last
few fires read from `ImportJob` (`trigger`, `status`, `created_at`) — so "is it working?" is
answered by the record of it working, not by a green dot. An agent with no schedule shows the one
thing that can be added and what it would do.

### D6 — The scheduler's *selection* moves; its tick does not

`repo.scheduled_instances(session, name)` changes its `FROM`: instead of
`agent_instance.schedule == name`, it joins `agent_schedule` on `active` and the instance's
`status == "deployed"`. Everything after that line in `app/sync/scheduler.py` is unchanged — the
stale-job reclaim, the window computed at fire time (0010 D4), the per-instance concurrency guard,
the job row, `execute_import_job`.

That is the test of whether this design is at the right seam: the clock's *behaviour* is not a
subject of this ADR, and no file in `app/sync/` changes except one query.

### D7 — The migration carries armed rows across, then drops the column

One Alembic migration: create `agent_schedule`; for each `agent_instance` with a non-null
`schedule`, create or find a `StudioAgent` for its tenant, attach the instance, and insert the
corresponding `agent_schedule` row; drop `agent_instance.schedule`. Existing armed practices keep
mirroring across the deploy without a manual step, which is the only acceptable outcome — the
migration must not require the SQL session it exists to abolish.

## Consequences

- **New:** `AgentSchedule`; `agent_id` set on workflow deployments; agent routes for arming and
  pausing; a Schedule section in the agent workspace; one migration.
- **Removed:** `agent_instance.schedule`; the DEPLOY.md §1 step 4 SQL snippet, which becomes a
  sentence pointing at the Studio.
- **No engine change, no template change, no connector change.** `availability_mirror.yaml` is
  untouched, and so is every governed path. One query in `app/sync/` changes.
- **ADR-0010 D3 superseded, its reasoning kept.** D1, D2, D4–D7 of 0010 stand as written.
- **ADR-0006's `Trigger` lands early, and narrower.** This is that record for the workflow tier
  under a concrete name. When the task tier needs triggers it should widen this table or sit beside
  it — and if `AgentSchedule` does not fit, that is evidence about the task design, the same test
  0006 D7 sets for `ImportJob` → `Task`.
- **Workflows and voice agents converge.** Both become `StudioAgent` + pinned instance + operational
  entrypoints. The agents index already renders them side by side; after this they are side by side
  in the model too, and `StudioAgent.draft` carrying a workflow's parameters is the next question
  (see below).
- **The duplicate-instance bug is closed by construction**, not by a cleanup script.

## Open questions

- **Does a workflow agent have a draft?** `StudioAgent.draft` holds a voice agent's config and is
  edited before publishing. A workflow's `param_values` live on the instance. The cheapest landing
  is that a workflow agent's draft is its parameter set and "publish" pins a new instance — but that
  is a second change to the publish path and this ADR deliberately does not decide it. Until it is
  decided, a workflow agent is created *at deploy time* with an empty draft.
- **What names the agent?** A voice agent is named by its owner. A workflow agent deployed from a
  template has no name yet; defaulting to the template's display name is obvious and probably
  wrong the moment a practice has two of them against different destinations.
- **`_BERLIN` is unused in `app/sync/scheduler.py`.** ADR-0010 D4 says the window is built in
  Europe/Berlin; `window_for` calls `date.today()`, which is UTC on Railway. Harmless at the
  configured fire times and self-repairing on the near tier, but the dead constant means the
  decision was written and not implemented. Out of scope here; worth its own small fix.
- **Should arming record who did it?** `VoiceChannel` does not, so this does not either, for
  symmetry. If either grows an actor, both should.
