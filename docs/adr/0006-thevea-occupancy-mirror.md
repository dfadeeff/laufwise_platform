# ADR 0006 — thevea occupancy mirrored onto the website, so booked times stop selling as free

- **Status:** Proposed (2026-09-11)
- **Deciders:** project owner + architecture session
- **Amends:** [0004](0004-governed-calendar-import.md) — its "Deliberately not building:
  bidirectional sync" is lifted for **one** narrow reverse flow: thevea's *occupancy* (when each
  room is taken, never who) is published to the healthyfeet website. It is not a record sync and
  not conflict resolution. 0004 D7 (append-only into thevea) is untouched — this process never
  writes to thevea; it is handed a read-only view of it.

## Context

The practice takes bookings in two places: the website (healthyfeet-podologie.de, public booking
flow) and thevea (phone, walk-in, doctolib imports). The import (0004/0005) carries website bookings
*into* thevea. Nothing carries thevea's occupancy back, so the website keeps offering times that are
already taken in thevea.

Today the owner closes those times **by hand, in code**: `BLOCKED_DATES`, `DATE_TIME_WINDOWS`,
`DATE_ALLOWED_TIMES`, `SINGLE_ROOM_DATES` in the site's `api/_lib/bookingConfig.ts` (mirrored in
`src/lib/booking/config.ts`), one commit + redeploy per change — the site's last ten commits are all
of this kind (`397974a Buchung: Fr 11.09. sperren`, …). The comment on `DATE_ALLOWED_TIMES` says it
outright: slots are "copied in from the merged Thevea calendar … offered where < 2 are occupied".

What the site has today (read from the site repo, 2026-09-11):
- `api/availability.ts` offers a 30-minute grid, 09:00–18:00, lunch 12–13, weekdays, today + 2
  months; capacity per slot = `SPECIALIST_COUNT = 2`; it subtracts site bookings by **exact start
  instant** (`preferred_date = slot`), status `new|confirmed|rescheduled`.
- `api/booking.ts` inserts atomically `WHERE (count at that instant) < capacity`.
- **No table for blocked time**, no machine write endpoint. Admin auth is HTTP Basic against
  `ADMIN_PASSWORD` — the credential laufwise already holds for the source connection.
- **`BERLIN_OFFSET = "+02:00"` is hard-coded.** From 2026-10-25 (CET) every slot instant is an hour
  off; any comparison against real UTC times is wrong for winter dates.

Owner decisions (2026-09-11):
1. The website gets **3 places per time — one per thevea room MA 1, MA 2, MA 3**, and they are
   fully synchronised with thevea. All three rooms always work in the site's hours; *free* is
   whatever thevea leaves free.
2. The mirror covers **the days selected** for the run (Today / Tomorrow / Next 7 days), not the
   whole booking horizon.
3. It runs **on the same button** as the import: import first, then the mirror. A schedule comes
   later.
4. **Only appointments occupy a room** (`PatientenTermin`, `SonstigerTermin`); absences (holiday,
   sick leave, Hausbesuch) do **not** — they stay a manual site restriction.

## Success criterion

For every selected day, after one press of the button:
1. The website offers a time only if **fewer than 3 rooms** are occupied then — counting each thevea
   room with an appointment overlapping `[slot, slot+30min)` once, plus website bookings at that
   time that are **not yet in thevea**. A website booking already imported into thevea is counted
   **once**, not twice.
2. The published occupancy is **verified by reading it back** from the website and comparing with a
   fresh thevea read (postcondition), never the push's claim.
3. **Idempotent:** a day whose website copy already equals thevea is a governed skip, not a write.
4. **Cancellations release the time:** an appointment removed or moved in thevea frees the old time
   on the next run.
5. **Nothing personal leaves thevea:** the website receives room, start, end and — for imported
   website bookings only — the website's own `HF-…` ref. No name, note, phone or procedure.
6. **Fail-closed:** a thevea or website read failure halts that day (`STATE_UNAVAILABLE`) and it is
   reported as failed; the website keeps its previous state for that day.
7. A day that was **never mirrored** behaves exactly as before the mirror: 3 places (owner raised
   `SPECIALIST_COUNT` to 3 on 2026-09-11, ahead of this ADR), site bookings only, manual config.

## Decision

### D1 — A separate governed process: `availability_mirror`, one day = one governed unit
A new template `backend/runbooks/availability_mirror.yaml`, not a v4 of `calendar_import`: the unit
differs (a *day's occupancy*, not an *appointment*), the direction differs, and so does the write.
Versions pin independently. Roles, not adapters (0004 D2) — with **new role names** so neither
process can be bound the wrong way round:

```yaml
template: availability_mirror
agent_class: workflow
required_connections: [occupancy, site]      # occupancy = thevea (read-only), site = healthyfeet
parameters: { window_from, window_to, rooms }   # rooms = the thevea rooms that are website places
state:
  site_day: { provider: site, query: day_matches_occupancy }
steps:
  - id: publish_day
    kind: enforced
    tools: [publish_busy_day]
    preconditions:
      - check: site_day.in_sync == false
        else: "the website already shows thevea's occupancy for this day — skip"
    execute: { adapter: registry, tool: publish_busy_day }
    postconditions:
      - check: site_day.in_sync == true
        else: "the website does not show thevea's occupancy (publish claimed success)"
```

`site_day` reads **both** systems live (thevea's day, the website's copy of that day) and reports
whether they are equal — the same shape as `DestinationPatientProvider`, which already grounds on a
live source read plus a destination read. The engine's DSL compares a binding to a literal, not
two bindings, so the comparison lives in the provider; it stays a pure read. Either read failing
raises `StateUnavailable`.

### D2 — Two narrow capabilities, not a widened `DestinationCalendar`
In `app/connectors/base.py`:

```python
@dataclass(frozen=True)
class BusyRange:            # no personal data by construction
    room: str
    start: str              # ISO instant, UTC
    end: str
    site_ref: str | None    # the website's own HF-… ref when thevea holds an imported website booking

class OccupancySource(Protocol):          # thevea implements it
    def list_busy(self, day: str, room_ids: list[int]) -> list[BusyRange]: ...
    def close(self) -> None: ...

class AvailabilityMirror(Protocol):       # healthyfeet implements it
    def read_day(self, day: str) -> list[BusyRange] | None: ...   # None = day never mirrored
    def publish_day(self, day: str, ranges: list[BusyRange]) -> None: ...
    def close(self) -> None: ...
```

- **thevea** gains `list_busy` — the existing `_GET_TERMINE` query over a **Berlin** calendar day
  (not the UTC day `_to_instant` builds today), filtered to `room_ids`, keeping only
  `__typename in {PatientenTermin, SonstigerTermin}` (an allowlist: an unknown type never occupies a
  room silently *or* frees one — see Open questions), mapped to `BusyRange` with `site_ref` taken
  from `bemerkung` by `HF-\d{6}-[A-Z0-9]{4}` (the site's `generateRef` format). `bemerkung` itself
  never leaves the connector. `DestinationCalendar` is unchanged; the mirror is resolved against
  `OccupancySource`, so the mirror run has no thevea write capability at all.
- **healthyfeet** gains `read_day` / `publish_day` over the new site endpoint (D4), same Basic auth.

`publish_day` **replaces** the website's copy of one day. This is deliberate and does not touch D7:
D7 protects *records* in thevea. The website copy is a derived, disposable projection with no
personal data; replace-per-day is the only way criterion 4 (a cancelled appointment frees its time)
can hold. It never touches the website's `bookings` table.

### D3 — Orchestration: one day at a time, reusing the import's job machinery
`app/sync/mirror.py::run_mirror(session, runtime, instance, window, on_progress)` walks the Berlin
days in `[window_from, window_to]` and runs the contract once per day (case `{"day", "rooms"}`),
classifying like the import: OK → *published*, BLOCK (in sync) → *skipped*, anything else →
*failed*. It returns the existing `ImportReport` with **days as refs**, persisted in the existing
`import_job` row — no migration; `app/sync/jobs.py` picks the orchestrator by template name. The
resolver (`resolve_connectors`) gains a branch for the `occupancy`/`site` roles next to the
`source`/`destination` one; the engine and runner are unchanged.

### D4 — The website: a mirror table, an endpoint, and occupancy-aware availability
In the site repo (`Podologie Healthy Feet`):
- **`db/0007_occupancy_mirror.sql`** (idempotent, like every migration there):
  `occupancy_days(day DATE PK, synced_at)` — which days are mirrored — and
  `occupancy_ranges(day, room, starts_at, ends_at, site_ref NULL)`.
- **`api/admin/occupancy.ts`**, Basic auth (`checkAuth`, as in the other admin routes):
  `GET ?day=` → `{ day, synced, ranges }`; `PUT { day, ranges }` → replaces that day in one
  transaction and stamps `occupancy_days`. JSON only, no email, no `bookings` access.
- **Availability on a mirrored day:** capacity **3**;
  `occupied = |distinct rooms with a range overlapping [slot, slot+30)| + |active site bookings at
  the slot whose ref is not a site_ref of that day|`; `available = max(0, 3 − occupied)`.
  The **same rule guards `api/booking.ts`'s atomic insert** — otherwise a page loaded before a sync
  could still book a taken time.
- **A never-mirrored day** keeps the pre-mirror rule (3 places minus site bookings) — criterion 7.
  Until a day is mirrored, the website knows nothing of thevea's phone/doctolib appointments, so
  the third place is an overbooking risk the mirror exists to close.
- **The manual restrictions keep applying on every day** (`BLOCKED_DATES`, windows, allowed times,
  lunch, `SINGLE_ROOM_DATES` as a cap). They can only remove slots, never add them, so keeping them
  is safe and is how absences stay closed (owner decision 4).
- **Prerequisite fix:** derive the Berlin offset per date instead of `BERLIN_OFFSET = "+02:00"`.
  Without it, every winter slot is compared an hour off against thevea's UTC instants.

### D5 — Studio: the same button, a second report
On `/studio/configure/calendar_import`, when the source is the healthyfeet account, the button runs
the import job, then deploys-if-needed the `availability_mirror` instance (bindings:
`occupancy` = the chosen thevea account, `site` = the chosen healthyfeet account; same window and
rooms) and runs its job; the panel shows a second, day-level report ("website: N days updated,
M already in sync, K failed"). `ConnectionRow`'s hard-coded "source = not thevea / destination =
thevea" becomes a role → allowed-adapters map (`occupancy: thevea`, `site: healthyfeet`), so the
mirror's own catalog page also works on its own.

## Consequences

- **New:** `availability_mirror.yaml`; `BusyRange` / `OccupancySource` / `AvailabilityMirror`;
  `TheveaConnector.list_busy`; `HealthyfeetConnector.read_day` / `publish_day`; a mirror provider +
  the `publish_busy_day` tool; `app/sync/mirror.py`; a resolver branch; the job dispatch. Site:
  migration 0007, `api/admin/occupancy.ts`, availability + booking changes, the DST fix.
- **No engine change, no platform migration.** The engine, runner and `import_job` table are reused.
- **`ImportJob` now also carries mirror runs** (refs are days). The name reads import-specific; a
  rename is not worth a migration yet.
- **Chained in the browser:** the mirror starts when the page sees the import finish. Closing the tab
  mid-import skips the mirror for that press. Acceptable for a manual button; the scheduled run
  (next step) belongs on the backend and removes this.
- **Freshness is "as of the last press".** A phone booking in thevea at 10:00 is visible to the
  website only after the next run. The manual process today is slower; a schedule closes the gap.
- **Two deploy orders:** the site first (migration 0007 run by hand — the site has no auto-migrate —
  then the code), then the platform. The site change is inert until a day is mirrored.
- **Tests:** platform — `MockTransport` connector tests (Berlin day boundaries, type allowlist, room
  filter, `HF-` ref extraction, no `bemerkung` leak) and a DB-backed mirror e2e in the style of
  `test_calendar_import.py` (publish, idempotent skip, cancellation frees, silent publish failure →
  REJECT, unreadable thevea → failed). Site — it has no test harness; the overlap/occupancy rule is
  a pure function tested with Node's built-in runner (`node --test`, Node 25 strips TS types), so
  no new dependency.

## Deliberately not building

A schedule / cron (next step, owner decision 3) · the whole booking horizon (owner decision 2) ·
absences occupying rooms (owner decision 4) · per-room assignment on the website (the site counts
places; which room is thevea's business) · writing anything into thevea from this process ·
any personal data on the website side · replacing the manual config (it keeps working; it just
stops being needed for what thevea already knows).

## Open questions

- **Does `termine` return absences and cancelled appointments, and under which `__typename` /
  status?** Verify on the practice's account before the first publish. The allowlist makes an
  unknown type fail safe for absences, but a *cancelled* `PatientenTermin` that still comes back
  would keep a time blocked — check for a status field.
- **Rooms not on the website.** Only the three `rooms` count; any other room id in thevea is ignored.
  Confirm MA 1–3 = `208413, 208416, 229566` is still the full Munich set.
- **Appointments across the lunch break or past 18:00** occupy only the slots the site actually
  offers — expected, noted.
