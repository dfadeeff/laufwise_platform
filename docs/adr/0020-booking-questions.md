# ADR-0020 — A practice's own booking questions, answered into the appointment's note

Status: accepted · 4 October 2026 · extends ADR-0019 (amended: no treatment list), ADR-0005

## Context

Once the treatment list was removed (ADR-0019, amended), the agent books one plain appointment, so
the practice's calendar no longer learns what the caller wants. Practices need different things
before a visit: a podiatrist needs the treatment and whether there is a prescription, a
physiotherapist needs the prescription, an agency something else entirely. Hard-coding any of
these into the platform makes it specific to one kind of practice.

## Decision

### D1 — Questions are configuration: a label and what to ask

`AgentConfig.booking_questions` holds up to ten `{label, ask, required}` entries, edited in
Instructions → *Questions before booking*. `label` is how the answer appears in the note
("Behandlung"); `ask` is what the agent says ("Welche Behandlung wünschen Sie?"). Labels are
unique, case-insensitively. They are pinned into the published snapshot with the rest of the
configuration.

### D2 — The answers are recorded as draft details, and required ones are enforced

- `appointment_set_details` takes `answers: {label: text}`. An unknown label is rejected, like
  any other detail the appointment does not have. Answers are kept verbatim, cut at 200 characters,
  and never judged.
- The next-step note asks the next unanswered required question once the personal details are
  complete. The runtime prompt lists every question as a hint.
- `book()` refuses while a required question is unanswered, before any write. This is the
  guarantee; the prompt is not.
- The answers are part of the confirmation fingerprint, so changing one after the read-back needs
  a new yes.

### D3 — Where the answers go

Into the appointment, as `raw["details"]` ("Behandlung: Hornhautentfernung · Rezept: ja"), in the
practice's order. Thevea writes it into `bemerkung` after the procedure and before the ref, which
stays last as the idempotency key. Answers are **not** in the summary email: a free-text answer
can describe a condition, and the email carries none (spec §3.9).

## Consequences

- The platform stays generic: a practice decides what it asks, and staff see the answers in the
  calendar.
- A system without a free-text note would need its own mapping for `details`. Doctolib cannot
  book by phone today, so the question does not arise yet.

## Open questions

- Choice answers (yes/no, a fixed list) instead of free text, if practices want to filter on them.
