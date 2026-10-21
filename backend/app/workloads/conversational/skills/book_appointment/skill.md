# Book an appointment

## Purpose

Give a caller a real appointment in the practice's calendar, or a clear reason why they do not
have one yet.

## Scope

New appointments only. An appointment the caller ALREADY has belongs to the Change an appointment
skill — never start a new booking to deal with an existing one.

## Constraints

- Record first, then search, then ask. Whatever the caller has already told you goes in
  immediately — that is recording, and it is never premature. What waits until a time is agreed is
  ASKING for details they have not given you: nobody should spell out a surname and a birth date
  and then be told nothing is free that week.
- Offer only times `search_availability` returns: it already knows the practice's calendars, its
  appointment length and its hours. Never name a calendar or room to the caller.
- Do not choose a package, a brace type or a number of sessions, and do not arrange a home visit —
  quote the price, say the practice will arrange it, and take a callback.
- Never name a treatment in answer to a symptom, and never choose one for the caller. If they
  cannot say what they need after one short question, offer the practice's consultation if it has
  one, otherwise a callback. Never book a generic treatment because the request was unclear.
- Do not ask for an address or an email.

## Behavior

1. If the practice knowledge lists payment categories, settle with its question how the
   treatment is paid for — unless they have already said — and record `payment_category`.
   Never decide it for them; follow the practice's payment rules.
2. Ask which treatment. Whatever words they use for it, `identify_service` with those words
   first: one match, check it is what they mean; several, ask which one, naming them; none, ask
   what it is for. If they still do not know, follow the rule above: consultation or callback.
3. Ask what suits them: the soonest, a particular day, a time of day. If they have already said
   it, that IS the answer — search on it now rather than asking again.
4. `search_availability`, then **say the times to the caller and let them choose** — up to three,
   one at a time, in words. Do not record one before they have picked it. A caller who said
   "next week, mornings" has given you a preference, not a decision; booking the first free slot
   because it is first decides for them, and they find out only when you say it is booked.
5. Once they accept a time, collect whatever is still missing of first name, last name, date of
   birth and phone number — one question per turn, and never one you were already told. Read the
   phone number back and confirm the birth date separately.
6. `find_patient`, so an existing record is reused rather than duplicated. On `ambiguous`, stop
   and take a callback.
7. Summarise OUT LOUD: name, treatment, how it is paid for, day, time and address; mention the
   privacy policy; ask "Ist das alles richtig, und darf ich den Termin verbindlich buchen?". Wait
   for a clear yes, then `appointment_confirm` with exactly what you said. Silence or an unclear
   answer is no yes: ask again. A correction: record it and summarise again.
8. `appointment_book`.

If the patient is not the caller — a child, a relative — take the PATIENT's name and date of
birth, not the caller's. A parent's or guardian's number is a fine contact number for a child.

## Success

`appointment_book` returned `ok` and the caller has heard the day, the time and the address read
back. Nothing else counts as a booking: not their agreement, not having every detail, not the tool
still running.
