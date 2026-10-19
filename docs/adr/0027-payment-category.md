# ADR-0027 — Every booking has a payment category, and the practice decides which fit

Status: accepted · 19 October 2026 · implements #132 (Mia specification v3, item 5) · builds on
ADR-0025 (service catalogue), ADR-0020 (booking questions)

## Context

Before booking, the specification requires knowing whether the caller has a statutory
prescription (Muster 13), a private prescription, or pays themselves; the treatment has to be
compatible with it; and the agent must never imply cover. Until now `appointment_set_details`
had a free `prescription` field nothing required, and a practice could only ask "Haben Sie ein
Rezept?" as a free-text booking question — recorded, not enforced.

## Decision

### D1 — Categories are agent configuration, compatibility is catalogue data

`payment_question` and `payment_categories` (key, label, the practice's own sentence) live in the
agent's configuration, next to its booking questions, and are pinned at publish. Each catalogue
service lists the category keys it may be booked with (`workspace_service.payment_categories`;
empty means any). A key is given once from the category's first name and then kept, so renaming
a category does not cut services loose. Nothing in the kernel names Muster 13.

### D2 — The platform enforces it

With categories set:

- the session asks for it first (it decides what is possible) with the practice's question;
- an unknown category is not recorded, and an incompatible treatment/category pair is refused
  whichever was said first, naming the categories the treatment does take;
- the booking contract (`voice_appointment` v5) has two preconditions, `payment_chosen` and
  `payment_allowed`; absent values fail closed. A practice without categories passes both;
- it is part of what the caller confirms (the confirmation fingerprint);
- the model's `payment_category` argument is an enum of the practice's keys, and does not exist
  for a practice without categories.

### D3 — Said, written, never promised

Once chosen, the category's own sentence is handed to the agent to say. The category's label is
the first item of the appointment's note in the calendar. The prompt states that a prescription
does not mean everything is covered and that a caller without one can pay themselves rather than
being steered into a consultation.

## Consequences

- Agents pinned to contract v4 keep booking as before until republished.
- The category is in the note, not in Thevea's own billing fields: a Muster 13 appointment is
  still created as the practice's "Privat" category, and staff attach the Verordnung. Writing it
  as a statutory (Heilmittel) appointment is a separate decision with Thevea's data model.
- The bundled eval agent has no categories, so the nightly replay is unchanged; the behaviour is
  covered by unit tests against the session and the contract.
