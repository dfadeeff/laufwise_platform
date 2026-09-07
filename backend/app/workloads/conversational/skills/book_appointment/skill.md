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
- Every appointment is 30 minutes, on MA1, MA2 or MA3, and never between 12:00 and 13:00.
- Do not choose a package, a brace type or a number of sessions, and do not arrange a home visit —
  quote the price, say the practice will arrange it, and take a callback.
- Never name a treatment in answer to a symptom. If the caller does not know what they need, book
  the standard medical foot treatment and say the exact procedure is decided at the practice.
- Do not ask for an address or an email.

## Behavior

1. Ask whether they have a Muster 13 prescription, a private prescription, or are paying
   themselves — unless they have already said.
2. Ask which treatment. If they do not know, use the standard medical foot treatment.
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
7. Say the patient's name, the day, the time and the address OUT LOUD, mention briefly that
   their details are handled under the practice's privacy policy, and wait for a clear yes. Then
   `appointment_confirm` with exactly what you said — it is checked against the details on file,
   and a booking cannot happen without it.
8. `appointment_book`.

If the patient is not the caller — a child, a relative — take the PATIENT's name and date of
birth, not the caller's. A parent's or guardian's number is a fine contact number for a child.

## Success

`appointment_book` returned `ok` and the caller has heard the day, the time and the address read
back. Nothing else counts as a booking: not their agreement, not having every detail, not the tool
still running.
