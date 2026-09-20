# ADR 0011 — a fourth room, and an absence closes the website's slot

- **Status:** Proposed (2026-09-20)
- **Deciders:** project owner + architecture session
- **Amends:** [0009](0009-thevea-occupancy-mirror.md) — owner decision 4 ("only appointments occupy
  a room; absences stay a manual site restriction") is **reversed**, and its success criterion 1
  ("fewer than 3 rooms") becomes four. Everything else in 0009 stands: one day is still the
  governed unit, the roles are still `occupancy`/`site`, the mirror still holds no write capability
  on the practice calendar, and 0004 D7 is untouched. [0010](0010-scheduled-availability-mirror.md)
  is unaffected — its tiers and staleness bounds now also carry absences.

## Context

Everything below was read from the live thevea account on 2026-09-20, not inferred.

**1. There are four staffed calendars, not three.** `mandantMitarbeiterListe` returns nine people;
four of them are Munich's rooms, and the practice now labels them with the practitioner's name:

| room | `mandantMitarbeiterId` | label in thevea | working hours |
|---|---|---|---|
| MA1 | 208413 | Nata Skorobogatov | Mon–Fri 09:00–12:00, 13:00–18:00 |
| MA2 | 208416 | Soumeya | same |
| MA3 | 229566 | Seher Aydin | same |
| **MA4** | **240570** | **Elena Gerlach** | same, `validFrom 2026-09-20` |

The remaining five entries are Augsburg's real people and must never receive an imported
appointment (0005). `mitarbeiterArbeitszeitenFuerZeitraum` confirms all four Munich rooms share
the one grid `practice.yaml` already encodes, so the hard-coded grid stays honest — for now.

The rename is free for us: `_GET_TERMINE` selects no name field
(`app/providers/thevea.py:83`), the binding is the numeric id, and the `MA…` string is our own
key. (In thevea the label lives in `person.vorname` / `person.nachname` — it used to read
surname `MA`, first name `1`.)

**2. Absences are real, imminent, and invisible to every channel we run.** The account already
holds 13 absences over the horizon, `FORTBILDUNG` and `URLAUB` — four of them Elena's, in October,
November and December. And both of our reads miss them:

- The **mirror** filters on `_OCCUPYING_TYPES` (`thevea.py:133`), which was written as if absences
  arrived inside `termine` as `__typename: "Abwesenheit"`. **They never do.** thevea keeps them in
  a separate root field, and the app's own `getTermine` document asks for all three at once with
  the same input:

  ```graphql
  query getTermine($from: Instant!, $until: Instant!, $personenIds: [Int!]!, $resourceIds: [Int!]!) {
    termine(input: {...})                             { ...Termin }
    mitarbeiterArbeitszeitenFuerZeitraum(input: {...}) { ...MitarbeiterArbeitszeit }
    mitarbeiterAbwesenheitenFuerZeitraum(input: {...}) { ...MitarbeiterAbwesenheit }
  }
  ```

  Ours is a narrowed copy that kept only the first field. The fixture in
  `backend/tests/test_availability_mirror.py:61` asserts against a node shape thevea does not
  send, so the test that "proves" absences are dropped proves nothing.
- The **voice agent** reads `termine_between`, which is unfiltered — but unfiltered over a payload
  that never contained an absence. So it offers an absent room too. The write then fails with
  `validationResult.errorTypes = ['ABWESENHEIT']` (that is what `TheveaAbsence` is), the
  postcondition refuses, and the caller is told no after agreeing a time. Governance holds; the
  call is still wasted.

So the present state is not "the website is more permissive than the phone". It is that **no
channel knows the practice is closed**, and the gap is covered by hand: `BLOCKED_DATES` in the
site repo lists `2026-09-14 … 2026-09-18` with the comment *"practice closed all week"*, which is
exactly the `FORTBILDUNG 2026-09-14 → 2026-09-18` sitting in thevea.

**3. The shape of an absence** (verified): `{id, from, until, personId, abwesenheitGrund,
bemerkung}`, with `from`/`until` as **dates**, inclusive at both ends — thevea's `14.09 → 18.09`
is the Mon–Fri week the owner blocked by hand. A **one-day** query returns a multi-day absence that
merely overlaps it (asked for 16.09, got `14.09 → 18.09`), so the mirror's per-day read needs no
widening. `personId` is the same room id as `mandantMitarbeiterId`.

## Success criterion

1. For every day in the booking horizon, the website offers a time only if **fewer than 4 rooms**
   are occupied at it, where *occupied* means an appointment **or an absence** — each room counted
   once, plus website bookings not yet in thevea.
2. A room absent on a day is occupied for **every** slot of that day, including when the absence is
   one multi-day entry.
3. The **reason** for an absence never leaves thevea. The website learns that a room is taken, not
   that someone is ill or on holiday.
4. The phone and the website agree, because both subtract the same absences from the same grid.
5. 0009's other criteria are unchanged: read-back verification, idempotent skip, cancellations
   release the time.

## Decision

### D1 — The fourth room is data, plus two constants

| Where | Change |
|---|---|
| import + mirror instances | `rooms` param gains `240570` |
| `runbooks/calendar_import.yaml:34`, `runbooks/availability_mirror.yaml:33` | `default:` gains it — **a new template version**, since a published version is immutable |
| `knowledge/muenchen.yaml:39` | `resources: [MA1, MA2, MA3, MA4]` |
| `skills/book_appointment/skill.md:19` | the prompt names four calendars |
| voice connection config | `rooms` gains `MA4 → 240570` |
| site: `bookingConfig.ts:123`, `config.ts:113`, `configParity.test.ts:41` | `SPECIALIST_COUNT = 4` |

The site needs nothing else: `occupiedAtSlot` counts **distinct room strings**
(`api/_lib/occupancy.ts:31-38`) and never interprets them.

**The two halves land together.** Capacity 4 while the mirror still publishes three rooms means a
slot with all three known rooms busy shows one place free — the exact failure 0009 exists to
prevent. Order: room id into the instance params first, then the site constant.

### D2 — The occupancy read asks thevea the question thevea answers

`_GET_TERMINE` gains the second root field, exactly as the app sends it:

```graphql
mitarbeiterAbwesenheitenFuerZeitraum(input: {from: $from, until: $until,
                                             personenIds: $personenIds, resourceIds: $resourceIds}) {
  id from until personId
}
```

One document, one round trip, the same variables — no extra load on thevea, and no new connector.

**We select `id from until personId` and deliberately not `abwesenheitGrund` or `bemerkung`.**
Criterion 3 is then structural rather than a filter we must remember: the reason cannot leak
because the process never holds it. This matters more than it looks — the live data already
contains `bemerkung: "Iryna - Urlaub"`, and a `KRANKHEIT` reason would be health data about an
employee. A closed room is all either consumer needs.

An absent room becomes, for each mirrored day it covers, **one `BusyRange` spanning that day**
(`site_ref` empty). Downstream nothing changes: the site sees a fourth kind of nothing-in-
particular occupying a room, the digest stays deterministic, the postcondition still re-reads both
systems.

`TheveaPracticeCalendar` subtracts the same absences from the practice grid, so the phone stops
offering what the write path would refuse (criterion 4). Hausbesuch needs no decision here: it is
entered as an ordinary appointment and has always occupied its room.

### D3 — The site's manual restrictions stay, for now

`BLOCKED_DATES`, `SINGLE_ROOM_DATES` and the rest are not removed. They overlap with what the
mirror now publishes, which is safe — both reduce availability, neither invents it — and they stay
the practice's way to close a day the platform cannot see. Prune them only after a horizon's worth
of runs shows the mirror carrying the same closures.

### D4 — doctolib needs nothing

Corrected after checking the connector rather than trusting its recon comment: **nobody has a
personal agenda in doctolib**, and the ids are not typed by anyone anyway. When `agenda_ids` is
empty — which is the Studio's default, and the field is marked optional there — `_discover_agendas`
reads the account's own list from `/api/accounts` and keeps every non-template agenda
(`app/providers/doctolib.py:307`). A fourth room changes nothing on that side: the connector is
read-only, and whatever it imports lands in thevea, where this ADR's occupancy rule already covers
it.

## Consequences

- The website can sell four parallel places instead of three, from the moment the constant flips.
- Entering `URLAUB` or `FORTBILDUNG` in thevea closes the website by itself, within 0010's bounds —
  ≤20 minutes for today…+7 days, ≤24 hours further out. Entering one by mistake removes capacity
  just as fast; deleting it restores the slots on the next tick, and both are in the run trace.
- The week of 14.–18.09 is the worked example: the practice blocked it by hand in the site repo,
  in a commit, while thevea already knew. That commit stops being necessary.
- An absence closes *free slots*; it never touches an appointment that already exists in that room
  — on 18.09 one appointment stood inside the Fortbildung week, and it stays.
- The voice agent stops agreeing a time it cannot write, so `TheveaAbsence` becomes what it should
  have been: a guard that never fires in normal operation.
- A whole-team absence closes the website entirely for those days. Correct, and it will look
  dramatic the first time.

## Deliberately not building

- **Writing availability to doctolib.** No write capability exists on that connector, and none is
  added here.
- **Per-room working hours from `mitarbeiterArbeitszeitenFuerZeitraum`.** All four rooms currently
  share the grid in `practice.yaml`, verified above. The day one of them works part-time, this is
  the seam — the same query already returns it, and the fixed grid becomes the wrong source of
  truth (§III: build it then, not now).
- **Per-room capacity or room preference on the website.** Rooms stay interchangeable; a patient
  books a place, not a person. The names now visible in thevea make "book with Soumeya" an obvious
  next request — a different feature, with a different booking model.
- **A UI for the `rooms` map.** Set once per practice; see open question 2.
- **Removing the site's manual restrictions.** D3.

## Open questions

1. **How the voice connection carries `rooms`.** `ConnectionCreate.config` is `dict[str, str]`
   (`app/schemas/connection.py:19`), so the nested map `_rooms_from` expects cannot be created
   through the API at all — today it could only be written straight into the database. A flat
   `"rooms": "MA1:208413,MA2:208416,…"` parsed in `_rooms_from` is the smaller change; not decided
   here because it is not needed until a voice instance is bound to the real calendar.
2. **`abwesenheitGrund` values beyond `URLAUB` and `FORTBILDUNG`.** Only those two occur in the
   live data today. D2 is indifferent to the value — it never reads it — so a new reason needs no
   code change, which is the point.
