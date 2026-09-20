# Voice agent review — 6 September 2026

Verdict: a credible governed voice prototype, but not yet demonstrated production-ready or
state of the art. The real thevea integration is a GraphQL connector for specific calendar
operations, not a general-purpose agent that operates arbitrary websites.

Reviewed the working tree, including substantial pre-existing uncommitted voice changes.
This review does not certify a deployed version. No real patient records were created or changed.

## Fixes made in this review

- Moved synchronous voice tools off the audio event loop. Serialized tools per call to protect
  mutable booking state; an interrupted in-flight operation keeps its lock through completion.
  Call summaries wait for pending operations. Provider exceptions produce an honest error result.
- Replaced start-time equality with interval overlap checks in thevea availability. Long and
  off-grid appointments now block every overlapping slot; malformed intervals fail closed.
- Corrected appointment lookup to use GraphQL's `patientId`, not mutation input's `patientenId`.
- Corrected the historical lookup's invalid datetime subtraction.
- Rejected unknown booking resources and applied the practice timezone to query bounds and
  calendar defaults. The voice prompt's date now agrees with its practice-local clock.

Changed implementation: `backend/app/providers/thevea_calendar.py`,
`backend/app/workloads/conversational/surface.py`, and the new
`backend/app/workloads/conversational/dispatch.py`.

## Remaining findings, ordered by impact

1. **P1 — Live calls can book into a rehearsal calendar.**
   `backend/app/workloads/conversational/calendar.py:79` returns the sandbox for an unbound
   instance; `backend/app/api/v1/telephony.py` accepts that result for inbound phone calls.
   Production numbers need an enforced real-calendar requirement or an explicitly announced
   test mode. This review preserved the existing rehearsal behavior.

2. **P1 — Booking verification proves existence, not the full requested outcome.**
   `backend/app/providers/sandbox.py:487` sets `booking_confirmed` from existence alone.
   `backend/app/providers/thevea.py:371` searches remarks using substring matching and its
   configured search rooms. Verify exact reference, patient, time, and selected room; ensure
   all mapped voice rooms are covered. Add mock-transport end-to-end booking tests with wrong
   patient/room/time and acknowledged-but-missing writes before a live acceptance test.

3. **P2 — Media tokens are reusable and process-local.**
   `backend/app/workloads/conversational/sessions.py:64` reads without consuming the token.
   Concurrent sockets can reuse one mutable calendar/conversation. A multi-worker deployment can
   also route the media socket to a process without its token. Introduce atomic single-use claims
   and a deployment-appropriate shared session design. Close calendar clients on expiry/end.

4. **P2 — thevea cancel/move capability is absent.**
   `TheveaPracticeCalendar` deliberately does not implement `AppointmentLifecycle`. The tools
   refuse and request a callback. Sandbox cancellation is not evidence of real cancellation.
   Implement and verify these only against documented/authorized thevea operations.

5. **P2 — Audio quality is unproven.**
   The eval runner explicitly skips audio-dependent scenarios. Text replays do not establish
   recognition quality, end-of-turn latency, interruption recovery, or telephone audio quality.
   The saved `backend/runs/evals/latest.json` contained 24 passes and 4 failures across 28 results
   when inspected; this is historical evidence, not a replay of these changes.
   Establish recorded German/Russian/English telephone scenarios with latency measurements,
   interrupted writes, silence, noise, and language switching. Compare model configurations on
   the same fixtures before changing the current `gpt-4.1-mini` default.

## Validation

- Reproduced four calendar defects with failing regression tests before implementation.
- Full backend suite: **285 passed, 18 skipped**, with three dependency deprecation warnings.
- Ruff passed for the implementation and tests changed here.
- Tests use mock transports and deterministic tool calls. No live thevea acceptance test,
  paid model replay, or end-to-end audio benchmark was run.

## Contemporary reference

Pipecat's [function-calling documentation](https://docs.pipecat.ai/pipecat/learn/function-calling)
provides explicit interruption and asynchronous tool options. The installed Pipecat source was
also inspected: interruptible handlers default to cancellation. External writes need their own
completion/reconciliation handling because cancelling an awaiting coroutine does not undo an
HTTP write. The dispatcher added here protects execution; audio-level result delivery after a
barge-in still needs integration validation.

## External-site acceptance criteria

Use a designated thevea test account and verified room mapping. Complete a real phone booking;
read back the exact patient, room, start and duration from thevea; test an overlapping appointment,
provider timeout, dropped call during a write, and repeated request without duplicate creation.
Then prove a staff callback for unsupported changes. Additional sites require their own connector
or bounded browser tools with the same governed preconditions and verified outcomes.
