# ADR 0007 — Inbound voice agent: read and book, never cancel

- **Status:** Proposed (2026-08-23)
- **Deciders:** project owner + architecture session
- **Relates:** first real instance of [0006](0006-agent-taxonomy.md)'s `conversational` tier;
  realizes [0002](0002-studio-concept.md) D10 (one agent per run) and D13 (Twilio inbound);
  **preserves [0004](0004-governed-calendar-import.md) D7 unchanged** — see D2.
- **Clarifies:** 0004 D7's capability statement — it seals *mutation*, not reads (D2 below).

## Context

The platform can import a practice's calendar headlessly. It has never answered a phone. ADR-0006
named the `conversational` tier; `app/workloads/conversational/` is still a placeholder and
`runbooks/praxis_appointment.yaml` (v1, `agent_surface: voice_agent`) has only ever run against an
in-memory fixture.

The ask: a voice agent that answers inbound calls for the practice, tells a caller about their
appointments, and books new ones.

The ask also included **cancel**, and that collided with ADR-0004 D7 — the write path is sealed by
the *absence* of the method, not by a rule. thevea's own API does expose delete mutations
(`docs/spikes/M0-thevea-connector.md`), so nothing external was stopping us; the constraint is ours.

**Owner decision (2026-08-23): "for now it should only write, not delete appointments, we try
safely for now."** That settles the fork. This ADR adds **no mutation capability of any kind**.

## Decision

### D1 — Scope: answer, read, book. Cancel is explicitly out, and the agent says so.

| Capability | In v1 | Why |
|---|---|---|
| Answer an inbound call | yes | ADR-0002 D13's number → instance → run path |
| Tell the caller their own appointments | yes | a read; gated on identity (D3) |
| Book into a verified free slot | yes | the existing `book_slot` enforced step, bound to real thevea |
| **Cancel or move an appointment** | **no** | would require a mutation method; D7 stands |
| Anything else | escalate | D8 |

A caller asking to cancel is **not a failure path**. The agent confirms what it heard, tells the
caller plainly that a person will handle it, and escalates (D8). **It must never imply it
cancelled anything.** That sentence is a hard rule in the agent's instructions *and* is guaranteed
by the tool set: there is no cancel tool to call.

### D2 — Two new **read** methods. The sealed write set is unchanged.

The voice agent needs two reads the connector does not have. Both are recon'd in M0 and unbuilt:

| New method | thevea operation | Used for |
|---|---|---|
| `list_appointments_for_patient(patient_ref, window)` | `TermineForPatient` | "what appointments do I have?" |
| `find_free_slots(window, category)` | `Terminfinder` / `Terminvorschlaege` | availability before booking |

**This does not weaken D7.** D7's capability clause reads *"There is no `update`/`delete` method
anywhere — the tool registry a destination run can call physically has no way to mutate or remove
an appointment."* The guarantee is about **mutation**. Adding reads leaves it literally true. After
this ADR the write set is still exactly one method:

```
DestinationCalendar
  reads:   find_appointment · find_patient · list_appointments_for_patient · find_free_slots
  writes:  create_patient · create_appointment
  absent:  update · delete · cancel · move        ← unchanged, and deliberately unrepresentable
```

If a future ADR wants cancel, it must say so in its own D-number and argue it. It cannot arrive as
a side effect of a feature.

### D3 — Reading appointments aloud is an **enforced** step, not a trace step

The governed loop has so far protected against bad *writes*. Here the risk is different and
sharper: **disclosing one patient's appointments to a different caller.** A phone line has no
password.

So the read is enforced, and its precondition is identity:

```yaml
- id: read_appointments
  kind: enforced
  tools: [list_my_appointments]
  preconditions:
    - check: caller.identity_verified == true
      else: "caller identity not verified — cannot disclose appointments"
    - check: consent.telephone_handling == true
      else: "no consent to handle this matter by phone"
  execute: { adapter: registry, tool: list_my_appointments }
  postconditions:
    - check: disclosure.scoped_to_caller == true
      else: "returned appointments outside the verified caller's record"
```

This generalizes the platform's thesis in a way worth stating: **an enforced step gates a
consequential act, and disclosure is one.** Prevention, not detection, applies to reads too.

### D4 — Appointment content is spoken, never persisted

ADR-0002 #11's PHI rule now binds the voice path: *"read transiently and never persisted; the
episode log records only check results (booleans / minimal non-PHI fields), never raw calendar
content."*

Concretely, the episode log for `read_appointments` records a **count and the two booleans** — not
dates, not types, not names. The audit trail proves the disclosure was authorized without becoming
a PHI store.

### D5 — Two clocks: the surface speaks, the engine acts

Unchanged from PLATFORM_PLAN §6.1 and ADR-0002 D7/D8. Pipecat runs STT→LLM→TTS over the Twilio
media bridge in the API tier and **never touches the engine**. `greet` and clarification turns are
`kind: trace` markers the surface emits into the shared episode log keyed by `run_id`. Only
`verify_patient`, `read_appointments` and `book_slot` dispatch to the durable engine.

Latency is covered by an **announcement** — "let me check that for you" — which is spoken but kept
out of conversation memory, so a two-second thevea round trip does not read as dead air.

### D6 — One agent owns the run

ADR-0002 D10's conversational binding, unchanged: no per-step executor, no brain-swap mid-call.

### D7 — `praxis_appointment` v2; v1 instances keep running

Versions are immutable (ADR-0002 D15). v2 adds `read_appointments`, binds `calendar` to the real
thevea connector instead of `memory`, and adds the escalation outcome. Instances deployed on v1
keep running v1 until explicitly redeployed.

### D8 — Escalation is a first-class outcome, not an error

Out-of-scope requests — cancellations above all — end the governed run as **escalated**, carrying a
short structured summary of what the caller wanted. v1 delivers that by transferring the call or
leaving the practice a note; when the `task` tier exists (ADR-0006 D6) it becomes a task with a
human decision. A run that ends escalated is a **success**, the same way a declined approval is a
path and not a fault.

## Consequences

- **`app/workloads/conversational/` stops being a placeholder.** It gains the Pipecat surface
  behind the existing `ExecutionAdapter` seam. The engine is untouched (CLAUDE.md §XII).
- **Two connector reads to build** plus their `StateProvider` bindings. No mutation, no new write.
- **Twilio media bridge in the API tier** (ADR-0002 D13) — the first time that path is built.
- **A new precondition family: identity.** `caller.identity_verified` has no provider yet. What
  counts as verification on a phone line is an open question (below), and it gates the whole
  read path — nothing discloses until it is answered.
- **Three enforced steps now round-trip during a live call.** This is the first real test of the
  two-clock separation under a latency budget; if it reads as slow, the fix is announcements and
  pipelining, **not** moving governance into the surface (PLATFORM_PLAN §8 risk #3).
- **Risk accepted:** a voice agent that can book but not cancel is a partial receptionist, and
  callers will ask for cancellation on day one. Mitigated by D8 making that a clean, honest handoff
  rather than a dead end — and by the agent being structurally incapable of pretending otherwise.

## Open questions

- **What verifies a caller?** Caller ID alone is spoofable and shared within families. Name + date
  of birth is the practice's own norm. This gates D3 and must be settled before any disclosure
  ships. Recommend: caller ID as a *hint* for lookup, DOB as the *check*.
- **Is the call transcript PHI, and how long is it kept?** D4 covers the episode log. The
  recording/transcript is a separate store and a separate retention decision. Do not default to
  keeping it.
- **Where does the voice agent's model config live?** Same open question ADR-0006 left — a
  `parameters` entry, a contract block, or instance config. `praxis_appointment` v1 already
  declares `llm` as a parameter; confirm that is the answer and close it.
- **Does the caller need to be an existing patient?** v1 assumes yes (`verify_patient` blocks
  otherwise). A new patient wanting a first appointment is a real case; ADR-0005 gave us
  `create_patient`, so it is buildable — but it widens identity risk and is deliberately deferred.
- **Escalation transport** for D8 — warm transfer, voicemail, or a note. Reuses ADR-0002's still
  open approval-transport question.
