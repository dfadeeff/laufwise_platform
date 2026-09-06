# ADR 0008 — Cancel and move by phone: two named transitions, not an update method

- **Status:** Accepted (2026-09-06)
- **Deciders:** project owner + architecture session
- **Drives:** `docs/Healthy_Feet_Voice_Agent_Technical_Specification_EN.md` v1.1 §3.6, §3.7, §4.4, §4.5
- **Amends:** [0007](0007-inbound-voice-agent.md) D1 — cancel and move move from "explicitly out"
  to "in, through this capability". The 2026-08-23 owner decision it recorded is superseded by the
  practice specification and by the owner decision of 2026-09-06.
- **Preserves:** [0004](0004-governed-calendar-import.md) D7 and [0005](0005-governed-patient-cards.md) D1
  — `DestinationCalendar` still has no `update` and no `delete`. See D1.

## Context

The practice specification requires the voice agent to reschedule (§3.6, §4.4) and to cancel
(§3.7, §4.5) an existing appointment. Cancellation is defined precisely: set the appointment's
status to `abgesagt`, *preserving the appointment and its history; do not permanently delete it*.

That collides with the platform's most load-bearing guarantee. `DestinationCalendar` deliberately
has no `update` and no `delete` — append-only is enforced by the fact that the capability does not
exist (ADR-0004 D7), and thevea's own `patientAktualisieren`/`patientEntfernen` are deliberately
not wrapped. ADR-0007 D1 resolved the same collision by ruling cancel out of scope entirely, on an
owner decision to "try safely for now".

The specification makes that answer no longer sufficient: acceptance tests §8.9–§8.11 require the
agent to actually move and actually cancel. Three ways out were considered.

| Option | Verdict |
|---|---|
| Add `update_appointment` / `delete_appointment` to `DestinationCalendar` | **Rejected.** Simplest code, but it deletes the guarantee for *every* connector, including the calendar-import path that has nothing to do with phone calls. A generic mutation cannot be scoped back afterwards. |
| Keep ADR-0007's answer: verify, then hand to staff | **Rejected.** Honest, and it was the right call while the requirement was ours to set. It is not, now: the practice has specified the behaviour and the agent would fail §8.9–§8.11 by construction. |
| A separate, narrow capability with exactly two named transitions | **Accepted.** |

## Decision

### D1 — A new protocol, `AppointmentLifecycle`, with exactly two methods and no others

```python
class AppointmentLifecycle(Protocol):
    def cancel_appointment(self, ref, *, reason=None, received_at) -> None: ...
    def reschedule_appointment(self, ref, *, new_start, new_resource) -> bool: ...
```

`DestinationCalendar` is untouched. A connector that does not implement `AppointmentLifecycle`
**cannot be asked to cancel anything** — the seam is opt-in per system, so adding this to the
sandbox and to thevea says nothing about healthyfeet or doctolib, which remain read-only sources.

Neither method destroys. `cancel_appointment` moves the record to a cancelled *status* and appends
to its history; `reschedule_appointment` moves the start and appends to the same history. There is
still no way, from anywhere in the platform, to make an appointment stop existing.

The distinction being preserved is not "no writes" — it never was. It is that **every write is a
named, enumerable transition**, so the set of things an agent can do to a patient's record is a
list someone can read, rather than the open set that `update(**fields)` would imply.

### D2 — Each transition is its own contract, at `risk: high`

`runbooks/voice_appointment_cancel.yaml` and `runbooks/voice_appointment_reschedule.yaml`. Not
steps inside the booking contract: a booking creates something that did not exist, while these act
on something the practice already has, and a wrongly cancelled appointment is a person turning up
to a slot that is gone. Separate contracts also mean the trace names which transition ran.

Both carry a check-only `identity_verified` enforced step before the acting step, so the trace
records *when* identity was established separately from what was done with it — the shape ADR-0002
established with `verify_patient`.

### D3 — A move is one transition, and it is atomic

`reschedule_appointment` tests the new slot **before** it touches the old appointment and returns
`False` without changing anything if it is taken (spec §3.6: "so the old appointment is not lost if
the new slot has already been taken"). It is a move, not a cancel-plus-create — a second
appointment where there should be one is a double booking the practice unpicks by hand.

The postconditions check both halves: the appointment is now at the new time **and** it is still
open. A cancel-and-recreate implementation fails the second; a claimed move that did not persist
fails the first.

### D4 — The requirements the specification states as behaviour are stated here as preconditions

A rule in a prompt is a habit the model can drift from. These are gates:

| Spec | Precondition |
|---|---|
| §3.4 — verify name, date of birth and the appointment's own date and time | `identity.verified == true` |
| §4.5 #2 — offer a move once, before cancelling | `identity.reschedule_offered == true` |
| §3.7 — the Ausfallhonorar wording inside the notice period | `identity.short_notice_acknowledged == true` |
| §4.5 #6 — a final confirmation | `identity.confirmed == true` |
| §3.1 — MA1/MA2/MA3, never the 12:00–13:00 break | `calendar.resource_allowed`, `calendar.slot_on_grid` |

`short_notice_acknowledged` and `reschedule_offered` are set by a platform tool
(`appointment_change_notices`) that *hands the agent the practice's approved sentences* rather than
asking it to assert it said them. What that proves is that the required wording was retrieved and
recorded before the write; the transcript is the evidence it was spoken. That is the strongest
guarantee available from outside the dialogue, and it is worth more than a boolean the model sets
about its own behaviour.

`identity.confirmed` is stronger than it looks: a confirmation is recorded over a *fingerprint* of
the details, so any later correction silently invalidates it. Nothing outside the dialogue can know
the caller really said yes, but the platform does guarantee that a yes stops counting the moment
anything it was a yes to changes.

## Consequences

- ADR-0007's "the agent must never imply it cancelled anything" survives in a stronger form: it is
  now a postcondition rather than a prompt rule.
- The append-only guarantee is narrowed, deliberately and visibly, from "the platform cannot change
  an appointment" to "the platform can make exactly two named, audited transitions on one, and
  cannot destroy one." Anyone reading `AppointmentLifecycle` sees the entire set.
- `test_the_calendar_seam_still_offers_no_generic_mutation` asserts the absence that remains, so a
  later `update_appointment` fails a test rather than passing review.
- **Open:** thevea exposes no known mutation for the `abgesagt` status transition or for moving an
  appointment, and the specification (§7) asks for an official API or written authorization. The
  capability is implemented on the sandbox calendar; binding it to thevea needs those identifiers
  from the practice. Until then, a deployed instance bound to thevea has no lifecycle capability
  and the agent falls back to a callback request — which is exactly what an opt-in protocol buys.
