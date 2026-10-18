# ADR-0014 — Practice systems: one registry entry per system, capabilities it declares

Status: accepted · 3 October 2026 · implements ADR-0012 L1 for the voice tier; extends ADR-0003,
ADR-0008 · **amended by ADR-0022 (proposed):** the registry pattern moves to the kernel and each
module registers its own systems; the capability vocabulary (`availability`/`patients`/`booking`/
`changes`) and `PracticeCalendar` become the `praxis` module's.

## Context

A practice is meant to open the Studio, connect the system its calendar lives in with its own
password, and put a voice agent on its phone line. The runtime was already system-agnostic: a call
binds to `PracticeCalendar`, `VOICE_CALENDARS` maps an adapter to a builder, and the agent cannot
tell which system answers. Everything around the runtime was not:

- The Studio could only create a Thevea connection (`adapter: "thevea"` in the page), and Phone
  setup only listed Thevea accounts.
- Activation refused any adapter but Thevea (`owned_connection`) and proved access with
  Thevea-specific calls (`check_calendar` → `termine_between`).
- "Booking" was assumed. A system that can read a calendar but not write to it had no honest
  place: turning booking off also removed availability (`_BOOKING_TOOLS` includes
  `search_availability`), so the agent fell back to answering questions only.

Doctolib is the test case. Its read API is verified live (ADR-0004 source connector, memory
`doctolib-pro-recon`); its write calls (patient search, appointment create) have never been
captured. An abstraction proven by a single implementation is not proven.

## Decision

### D1 — A practice system is one `CalendarSystem` entry, and it carries everything system-specific

`VOICE_CALENDARS` maps an adapter to a `CalendarSystem`, not a bare builder:

| Field | What it holds | Thevea | Doctolib |
|---|---|---|---|
| `label` | name the Studio shows | Thevea | Doctolib |
| `connect` | which connect flow the Studio runs | `password` | `password_and_code` |
| `mapping` | the config key, label and id kind for a calendar label → system id map | `rooms`, "Thevea room ID", numeric | `agendas`, "Doctolib agenda ID", text |
| `capabilities` | what the system can do on a call | availability, patients, booking | availability |
| `build` | connection + practice → the live calendar | `TheveaPracticeCalendar` | `DoctolibPracticeCalendar` |
| `verify` | proves authenticated reads of the mapped calendars | `occupancy_between` (was `termine_between`; renamed) | agenda appointments read |

Activation, the agent's connection check, the Studio's system list and the connect form all read
this entry. Adding a third system is one provider plus one entry; nothing in the Studio, the
activation path or the runtime changes (CLAUDE.md §XII). `GET /connections/systems` exposes the
non-code fields to the Studio.

### D2 — Capabilities are declared by the system and intersected with the agent's

`capabilities ⊆ {availability, patients, booking}`. The effective agent for a call is computed once,
in `prepare_voice`, from the published config and the bound system (`effective_config`):

- no `booking` → `booking_enabled` is off for the call and the booking and change skills are
  dropped;
- `availability` without `booking` → the opt-in `check_availability` skill is added: tell the
  caller what is free, then pass their chosen time to staff as a callback.

A capability can only be taken away here, never granted (the rule `capabilities.resolve` already
states). The published snapshot is not modified: when a system gains `booking`, the same published
agent books on its next call.

`check_availability` is opt-in (`"default": false` in its manifest), so it never appears in the
knowledge-base agent, the eval replay or an agent whose system can book, and `prompt_sha` for those
is unchanged. Its tool, `search_availability`, is read-only, so it survives booking being off when
a read-only skill claims it.

### D3 — Availability is derived the same way for every system

Neither Thevea nor Doctolib has a "free slots" query. Both derive availability as the practice's
own grid minus what the system reports booked, per mapped calendar. That subtraction moves into a
shared base (`DerivedAvailability`); a system supplies only `_occupied(start, end)`. An unreadable
appointment raises rather than freeing a slot (fail-closed, ADR-0003 D4). For Doctolib, every row
occupies its slot unless its status is an explicit cancellation, so an unknown status can only make
the agent offer less, never a taken time.

### D4 — Doctolib connects with password + emailed code, through the existing login job

The Studio runs the two-step flow `connections.py` already serves (headless login, code on a new
device, `pin_login` for unattended re-login). The calendar mapping is collected on the same form and
stored as `agendas` beside the existing `agenda_ids` the import path reads.

## Consequences

- A practice on Doctolib can connect, map, verify and activate without anyone writing code. Its
  agent answers questions, reads real availability and takes booking requests as callbacks with the
  chosen time. When the write calls are captured, `DoctolibPracticeCalendar` gains the booking
  methods, the entry gains `patients` and `booking`, and published agents start booking.
- A live call may trigger a Doctolib re-login (headless Chromium) if the stored session died. That
  takes seconds; the platform's filler covers it, but the first read after a long idle is slow.
- Two registries remain by design: `build_connector` (import connectors, per role) and
  `VOICE_CALENDARS` (practice systems a call binds to). They answer different questions.

## What this does not change

The governed booking path, the runbooks and `PracticeCalendar` itself. A system that cannot book
never reaches `appointment_book`, because the tool is absent, not refused.

## Open questions

- Doctolib's write API: patient search and create appointment need one live capture.
- Whether `rescheduled` Doctolib rows still hold their slot. Treated as occupying until confirmed.
- MCP-backed systems (ADR-0012 L2) would declare capabilities the same way; not built.

## Implementation status (2026-10-18)

- **Built as decided:** `CalendarSystem` and `VOICE_CALENDARS`
  (`app/workloads/conversational/calendar.py`); `effective_config` there, applied in
  `prepare_voice` (`app/agents/runtime.py`); `GET /connections/systems`
  (`app/api/v1/connections.py`); `owned_connection` / `check_calendar` (`app/agents/service.py`);
  `DerivedAvailability` (`app/providers/derived_availability.py`) with `_occupied` in both
  `thevea_calendar.py` and `doctolib_calendar.py`; `check_availability` opt-in
  (`"default": false`).
- **Renamed:** Thevea's verify read is `occupancy_between`, after `client.verify()`;
  `termine_between` no longer exists (fixed inline in D1).
- **Grown since (ADR-0021):** Thevea also declares a fourth capability, `changes`. Only the Studio
  reads it (the Capabilities section, `ConfigEditor.tsx`). `effective_config` keys only on
  `booking`, and `capabilities.resolve` withholds `cancel_appointment` / `reschedule_appointment`
  from every configured agent whatever the system declares (`capabilities.py` `_LIFECYCLE_TOOLS`).
- **Still open:** the Doctolib write calls (it remains `{"availability"}`) and MCP-backed systems.
