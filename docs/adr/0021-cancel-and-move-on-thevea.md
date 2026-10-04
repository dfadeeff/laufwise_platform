# ADR-0021 — Cancel and move on Thevea, inside a practice-set cancellation window

Status: accepted · 4 October 2026 · implements ADR-0008 on Thevea · supersedes the "absent on
Thevea" note in `thevea_calendar.py` and the hard-coded 24-hour notice period

## Context

ADR-0008 gave the platform exactly two transitions on an existing appointment, cancel (status, not
delete) and move, behind governed contracts. The sandbox had them and Thevea did not: we had not
been given Thevea's mutation, and the practice specification asked for authorization before we
went looking. On 4 October 2026 the practice owner asked for both on Thevea, and for the
cancellation window to be configuration rather than code.

## Decision

### D1 — Thevea's own update, copying everything it was given

The app's persisted-query map names `updatePatientenTermin`; the server reports its variables as
`id` and `terminInput: PatientenTerminInput` — the type the create takes. It REPLACES the
appointment, so `TheveaConnector.update_patienten_termin` copies every field from the appointment
as just read (`_TERMINE_FIELD` now selects `sequenceId patientenTerminArt terminfarbe kategorie
resources` too) and changes only:

- cancel: `status: "ABGESAGT"`, and a note in front — "ABGESAGT per Telefon <when> (<reason>)" —
  so our booking ref stays last;
- move: `from`, `until` (length kept) and the room, after checking the new slot is free.

Never `ignoreValidation`. Verified live on 4 October 2026 against the practice account, on the
test appointment only (moved 10:30 → 11:30, then cancelled; both read back).

### D2 — A caller's appointment is found by its Thevea id

`appointments_for` hands out Thevea ids, so an appointment the practice entered itself can be
verified, found again and changed; `find_appointment` still finds our own bookings by the ref in
the note. `history_of` returns the record's current state when it can be re-read — what the
cancellation postcondition needs ("it survived") — and claims no past events Thevea does not give.

### D3 — The window is the practice's, and inside it nothing changes by phone

`AgentConfig.cancellation_free_hours` (default 24, what every agent had) and
`cancellation_policy` (the practice's exact sentence; empty keeps the neutral default). Inside the
window `change_notices`, `cancel` and `reschedule` all refuse before any contract runs: the agent
says the policy as written and takes a callback request; staff decide. The owner chose this over
"change it and flag it" on 4 October 2026.

### D4 — The Studio knows which systems can change

Thevea's registry entry declares `changes`; the Capabilities section says whether the bound
system supports moving and cancelling, and holds the two fields.

## Consequences

- Callers can move and cancel by phone on Thevea; nothing can be deleted, and every change is a
  named, governed transition whose postconditions re-read Thevea.
- A field Thevea adds to `PatientenTerminInput` later and we do not read would be reset by a
  change — the read list in `_TERMINE_FIELD` must grow with it.

## Open questions

- A practice that wants late changes made and flagged instead of refused.
- Thevea's `TerminabsageSenden` (it notifies the patient) is deliberately not used.
