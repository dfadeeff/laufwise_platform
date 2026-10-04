# ADR-0018 — A Studio test call may use the practice's real calendar, read-only or writing tests

Status: accepted · 3 October 2026 · extends ADR-0014 (practice systems), ADR-0007

## Context

The Studio's test call always used the sandbox: it offered times from the practice's configured
hours and booked into memory. That kept a rehearsal from ever touching a real calendar, but it
also meant nobody could see the agent read real availability, or prove it can write into Thevea,
without a live phone number and a real call.

## Decision

### D1 — Three modes, chosen per test call

| Mode | Reads | Writes |
|---|---|---|
| `sandbox` (default) | the practice's grid | into memory |
| `read` | the practice's real calendar, through the chosen connection | nothing; the booking stops where it would write |
| `write` | the real calendar | real appointments, labelled as tests |

The test names the connection to use, which must be one of this practice's voice-capable accounts
(`owned_connection`). The calendar is built by the system's registry entry, exactly as on a live
call, and the agent is limited to what that system can do (`effective_config`). A Doctolib test
therefore cannot book, in any mode.

### D2 — Writing is a decision, never a default

`write` requires `confirm_real_writes: true` in the request, and the Studio asks for it with a
checkbox that says what will happen: real appointments marked "TEST", and possibly a patient card
for the name the tester gives. The request is refused without it.

### D3 — Where each mode acts

- `read`: the booking session stops when everything a booking needs is present and confirmed,
  and tells the agent to say nothing was booked. Missing details still produce the engine's normal
  refusal, so the test exercises the same checks.
- `write`: the governed booking runs for real, including the postcondition read-back. The
  appointment's note starts "TEST ·", so it is recognisable as a test. It is never cancelled
  automatically: to cancel or move it, make another call and ask the agent (ADR-0021). Nothing is
  ever deleted.
- Both are rehearsals: no summary email, no caller memory. The conversation records the calendar
  and the mode.

The mode and connection travel in the call's admission row (`voice_call_token`), so the media
socket rebuilds the same calendar wherever it runs.

## Consequences

- A practice can watch its agent read real availability, and prove one real booking, from the
  browser before giving it a phone number.
- A `write` test leaves real appointments in the calendar. They are cancelled or moved the same
  way as any other: by another call to the agent (ADR-0021). They are never deleted, and never
  cancelled automatically.
