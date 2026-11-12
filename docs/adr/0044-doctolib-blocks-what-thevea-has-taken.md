# ADR-0044 — doctolib blocks what thevea has taken

Status: accepted · 12 November 2026 · built, preview first · follows ADR-0009 (the occupancy
mirror) and ADR-0010 (running it on a schedule) · keeps ADR-0004 D7 (no appointment is updated
or deleted) unchanged

## Context

The practice takes bookings in two calendars. Patients book online in doctolib, and the practice
books by phone and in person in thevea. Each doctolib treatment-room agenda stands for one thevea
room: Behandlungsraum 1 is MA 2, and Behandlungsraum 2 is MA 3 (the import places appointments by
this pairing since #236). A time taken in thevea stays bookable in doctolib until someone sets an
"Abwesenheit" there by hand. The practice does this every day, and a time it misses can be sold
twice.

Doctolib Pro's own calendar requests were captured on 11 November 2026, and a test block was
created, read, changed and deleted. A block is `POST /api/events` with `type: "blck"`, read with
`GET /api/events/{id}`, and removed with `DELETE /api/events/{id}?edition_mode=occurrence…`.
No endpoint lists blocks: `GET /api/events?…` and `/calendar_display/events` both return 404, and
the appointment list leaves blocks out.

## Decision

**D1 — A new runbook, `doctolib_block_mirror`.** It uses one governed contract per day, like
`availability_mirror`. It has two roles:
- `occupancy` reads thevea. It has no write capability.
- `blocks` reaches doctolib's blocks only.

The import's `source` role is not used, so binding the account the import reads can never hand it
a write.

**D2 — What a day should have.** For each `room:agenda` pair:
- take the room's taken times in thevea (appointments and whole-day absences);
- join the times that touch, rounded to whole minutes;
- subtract every time the agenda already has a live doctolib booking, because a block over an
  appointment may suspend it.

Time already past is left alone.

**D3 — A ledger of its own blocks (`doctolib_block`).** Because doctolib cannot list blocks, the
mirror records the id of every block it creates the moment doctolib returns it. It reads each
recorded block back by id, and it deletes only blocks that are in the ledger. A block someone set
by hand is never seen, so it is never changed or removed. A recorded block someone deleted is
marked removed. The day is then compared again, so a block that is still wanted is set again.

**D4 — Create first, then delete.** A time that moves is never open in between. The postcondition
plans the day again from fresh reads of both systems, so the tool's claim never decides.

**D5 — Preview first.** The instance parameter `write` is `"no"` by default. Until it is `"yes"`,
a run plans every day from the same reads and the same `plan_day`, and writes nothing. The report
lists:
- days that are already right, as skipped;
- days that would change, as excluded, with the blocks it would set and remove in Berlin time;
- days it could not read, as failed.

**D6 — A block carries no patient and no label.** Its title and note are empty, so doctolib shows
a plain "Abwesenheit", the same as one the practice set by hand (amended 12 November 2026; the
first version read "laufwise · belegt (thevea)"). Only the agenda and the times are sent. The
mirror knows its own blocks by the ids in its ledger, never by their text.

## Consequences

- The schedule worker runs it the same way as the other two runbooks (`SCHEDULABLE_TEMPLATES`).
- Each day reads thevea once, doctolib's bookings once per agenda, and each recorded block once,
  both before and after a write. Run it a few times a day, not every minute.
- A doctolib write must carry the calendar page's CSRF token. The token is read from
  `<meta name="csrf-token">`, as the browser does. This is verified against the captured browser
  request, not yet from the server's own client: the first run with `write: "yes"` proves it, and
  a refused write fails that day loudly.
- If doctolib asks for an emailed code on re-login, the run stops until the account is signed in
  again in the Studio, as the import does.
