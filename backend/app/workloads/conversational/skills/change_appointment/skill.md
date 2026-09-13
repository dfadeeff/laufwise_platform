# Change an appointment

## Purpose

Move or cancel an appointment the caller already has — after establishing that it is theirs.

## Scope

Existing appointments only. A caller who wants an additional appointment belongs to the Book an
appointment skill.

## Constraints

- **Verify first, disclose nothing before you have.** Identity is exactly three things: the name,
  the date of birth, and the date and time of the appointment itself.
- **Never ask for a phone number to verify anyone.** It is not part of the check. A patient may
  ring from a work phone or a relative's, and refusing them would be wrong; a matching number is
  not on its own an identity.
- If verification fails, say nothing at all — not the date, not the treatment, not whether they
  have an appointment. "I can't confirm that on this call, but the practice will ring you back."
- If they have more than one appointment, do not read them out and do not say how many. Ask them
  to name the date themselves.
- Cancelling does not erase anything. The appointment is kept and marked cancelled, and you may
  say so.
- Say every sentence `appointment_change_notices` gives you, as written. They are the practice's
  own wording about money.

## Behavior

**Moving.** Verify → notices → find a genuinely free new time → read the old and the new
appointment back → confirm → `reschedule_appointment`. Tell them both times, and only after `ok`.

**Cancelling.** Verify → notices → offer the move once, and accept a no without pushing again →
say the Ausfallhonorar sentence when the notices include it → final yes → `cancel_appointment`.

## Success

The tool returned `ok` and the caller has heard what actually changed. A caller who could not be
verified has a callback request and has learned nothing about anybody's appointment.
