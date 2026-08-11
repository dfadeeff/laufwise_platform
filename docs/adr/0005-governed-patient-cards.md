# ADR 0005 — Governed patient cards: the import writes patient-bound appointments

- **Status:** Proposed (2026-08-02)
- **Deciders:** project owner + architecture session
- **Amends:** [0004](0004-governed-calendar-import.md) — the destination gains a *second* write
  capability (create a patient card) and the imported appointment becomes **patient-bound**
  (`addPatientenTermin`) instead of a patient-less room appointment (`addSonstigerTermin`).
  0004's D3 (one appointment = one governed unit), D4 (orchestrator above the engine), D4a
  (background job) and D7 (append-only) all still hold; D6's idempotency identity is unchanged.
  0003's credential custody, tenancy and anti-fabrication rule are untouched.

## Context

Today the import writes a **patient-less** `SonstigerTermin` into a thevea room and stuffs the
patient's contact details into the appointment's free-text `bemerkung`
(`app/providers/thevea.py::create_appointment`). thevea therefore has no patient record for an
imported person — the data exists only as a note on a calendar entry.

The owner's driver is **invoicing, not tidiness** (2026-08-02): creating a `Rechnung` in thevea
means retyping the patient's data by hand even though that same data already sits in the
appointment's `bemerkung`. With a real card the Rechnung flow can search the patient and pull the
data in. `Abrechnung` is done as a separate step anyway.

**Live recon against the practice's thevea account (2026-08-02)** settled the mechanics — every
claim below was verified, not inferred:

- `patientAnlegen($input: PatientInput!)` creates a card. Required: `vorname`, `nachname`,
  `sichtbar`, `terminErinnerungPerEmail/PerSMS`, `geburtsdatum`, plus the wrappers `anschrift`,
  `kontakt`, `krankenversicherung` — **every field inside those wrappers is optional**, so the
  practice's insurance data is *not* required to create a card.
- `addPatientenTermin($input: AddPatientenTerminInput!)` → `{terminInput: PatientenTerminInput}`.
  Required: `from`, `until`, `mandantMitarbeiterId`, `patientenId` (**note the spelling — reads
  expose `patientId`**), `patientenTerminArt` (`PRAXIS|HAUSBESUCH|VIDEOTHERAPIE`), `sequenceId`,
  `terminfarbe`. Optional and present: `bemerkung`, `kategorieId`, `clientId`, `ignoreValidation`,
  `status`, `resourceIds`, `verordnungId`. **There is no `title`** — it exists only on
  `SonstigerTermin`.
- `Termin` is an **interface**: `id`, `from`, `until`, `bemerkung`, `mandantMitarbeiterId` are
  interface-level; only `title` (Sonstiger) and `patientId`/`patient`/… (Patienten) are narrowed.
- Writing outside the practitioner's working hours is refused with
  `validationResult.type = ERROR`, `errorTypes = ['ABWESENHEIT']`. thevea's own
  `mitarbeiterArbeitszeitenFuerZeitraum` reports Mon–Fri `09:00–12:00` + `13:00–18:00` for all
  three Munich rooms.
- The three Munich rooms are `208413` (MA 1), `208416` (MA 2), `229566` (MA 3). The other five
  `mandantMitarbeiterListe` entries are real people and must never receive imported appointments.
- thevea **allows** two or three appointments in the same room at the same time; the owner
  considers overlap a non-issue because the source systems already enforce the schedule.

An end-to-end test (create card → create bound appointment → find by `bemerkung` → delete both)
ran green against the live account and left no residue.

## Success criterion

An import run over a source appointment carrying patient data:

1. Leaves a **patient card in thevea** holding first name, last name, date of birth, address
   (street + number, postcode, city) and email — the owner's complete field list, nothing more —
   and that card's existence is confirmed by **thevea's own read**, never by the write's claim.
2. Creates the appointment **bound to that card**, carrying the procedure name and the source ref.
3. Stays **idempotent**: a re-run creates neither a second appointment nor a second card *for that
   source appointment*.
4. **Never mis-attaches**: an uncertain patient match produces a new card rather than binding the
   appointment to a different human.
5. Lands in one of the three Munich rooms; if all three refuse with `ABWESENHEIT`, it is written
   **forcibly** and reported as such, so the owner can resolve it by hand.
6. Preserves **append-only**: nothing in thevea is ever updated or deleted by the agent.

## Decision

### D1 — The destination gains patient capability; append-only stays structural
`DestinationCalendar` (`app/connectors/base.py`) grows exactly two methods:

```
find_patient(candidate) -> PatientRef | None      # read, for matching + verification
create_patient(patient)  -> PatientRef            # the second (and last) write capability
```

`create_appointment` becomes patient-bound: it takes the resolved `patient_id`, the target
`room_id`, and a `force` flag (D6).

thevea *does* expose `patientAktualisieren` and `patientEntfernen`. **We deliberately do not wrap
them.** ADR-0004 D7's guarantee — "append-only is enforced by the absence of the capability" —
therefore still holds verbatim for both entities: the tool registry a run can reach has no way to
modify or remove an appointment *or* a patient card. Duplicate cleanup stays a human action in
thevea's UI (D3).

### D2 — `ensure_patient` is a separate governed step, verified by its own postcondition
The alternative — hiding find-or-create inside the appointment tool — was rejected: nothing would
then verify that the card actually exists, and an appointment bound to a card that was never
created is data corruption, not a failed import. The contract becomes two enforced steps:

```yaml
state:
  source_appt:  { provider: source,      query: appointment }
  dest_patient: { provider: destination, query: patient_for_appointment }   # NEW
  dest_match:   { provider: destination, query: appointment_by_ref }
steps:
  - id: ensure_patient
    kind: enforced
    tools: [create_patient]
    preconditions:
      - check: source_appt.exists == true
        else: "the source appointment no longer exists"
    execute: { adapter: registry, tool: create_patient }
    postconditions:
      - check: dest_patient.exists == true
        else: "thevea has no patient card for this appointment (write claimed success)"
  - id: copy_appointment          # unchanged from v2 except that it now binds the patient
    ...
```

**The step is deliberately not preconditioned on `dest_patient.exists == false`.** A BLOCK there
would halt the run exactly when the patient already exists — the *good* case — and the appointment
would never be written. The governed outcome is "*after this step a card provably exists*", not
"we created one"; the tool may find or create, and the postcondition decides the truth by
re-querying thevea. This keeps the CheckEvaluator a pure function of state (PLATFORM_PLAN §8
risk #2) and needs no conditional steps, which the engine deliberately does not have.

**Honest limit, stated once:** the engine can assert that a card *exists*, not that it is
*unique*. "Is this the same human?" is not a predicate over state, so no postcondition can prevent
a duplicate card. Duplicate prevention lives in D3's matching rule and is, by the owner's explicit
decision, best-effort.

### D3 — Matching rule: prefer a duplicate over a wrong match
Owner's decision (2026-08-02): duplicate cards are acceptable and cleaned up by hand; a wrong match
is not, because it silently attaches one person's appointment to another's card.

- Search `patientUebersicht(search=<nachname>)` and compare candidates locally on normalized
  (`nachname`, `vorname`) **and** `geburtsdatum`.
- Bind to an existing card **only when name and date of birth both agree**. Anything else — no
  candidate, name-only agreement, ambiguity — creates a new card.
- Email may corroborate a name+DOB match; it may **never** establish one alone. Families and
  relatives routinely share one address.
- This removes the approval gate an ambiguous-match design would otherwise need.

### D4 — Missing dates of birth: a fixed sentinel that never matches
The practice often omits the date of birth or enters a junk value (owner, 2026-08-02), yet
`geburtsdatum` is the only discriminator strong enough to match on. Decision:

- Absent or out-of-range date → the fixed sentinel **`1911-01-01`** (thevea rejects dates more
  than 120 years old or in the future; 1911 clears that until 2031). One fixed value, not "any
  implausible date", so such cards are findable with a single query.
- **A sentinel date is never treated as a DOB match.** Without this rule every unknown-DOB patient
  sharing a surname would collapse into one card — the exact mis-attachment D3 exists to prevent.
- Dates are sent as midnight UTC. Berlin is always ahead of UTC, so the calendar date cannot slip
  backwards; end-of-day values would not be safe.
- **A source's own date format is normalised to ISO before the destination sees it**
  (`patient_from_appointment`). healthyfeet's booking JSON carries the German day-first order
  (`26.01.1988`), doctolib carries ISO. Read literally, healthyfeet's form is unparseable and every
  card would get the sentinel — so *every visit of the same person would open a new card*, the
  duplicate D3 exists to limit. Day-first is the reading for both, since both serve one German
  practice; anything unreadable is passed through for the destination to substitute.

### D5 — Card content, appointment content, and the ref
- The card carries only the owner's field list: `vorname`, `nachname`, `geburtsdatum`,
  `anschrift{strasseUndHausnummer, postleitzahl, ort}`, `kontakt{email}`. `krankenversicherung`
  is sent as an empty wrapper. Reminder flags are set **false** — the agent must never cause
  thevea to email or SMS a patient.
- The card's address has three fields but healthyfeet composes it into one line. It is split on the
  5-digit postcode; a line without one stays whole in the street field rather than being guessed
  apart into a wrong city. doctolib sends the parts separately and is never re-split.
- The card's `bemerkung` carries an import marker, the source system and the source ref, so our
  own creations are findable by an exact query rather than by fuzzy duplicate detection. It also
  records when the date of birth is a D4 sentinel.
- The appointment's `bemerkung` keeps its current shape minus the contact details that now live on
  the card: procedure name, the forced-write marker when applicable (D6), and the source ref
  **last**. `find_appointment` still matches `ref in bemerkung`, so ADR-0004 D6's idempotency
  identity is unchanged. Since `PatientenTermin` has no `title`, the procedure name has nowhere
  else to go; wiring it into thevea's Verordnung/Heilmittel subsystem is explicitly out of scope.

**Required fix, or this ADR silently breaks idempotency:** `_GET_TERMINE`
(`app/providers/thevea.py`) currently selects `id/from/bemerkung` *inside*
`... on SonstigerTermin`, so a patient-bound appointment returns only `__typename` and is
invisible to `find_appointment`. Consequences if missed: the idempotency precondition always
passes (a duplicate on every re-run) *and* the postcondition always fails (every import reports
failure while actually writing). Selecting those fields at the `Termin` interface level fixes
both; verified live.

### D6 — Room placement ladder, and the forced write, live in the orchestrator
`ABWESENHEIT` means the room is absent (vacation), not that the appointment is wrong — the source
systems already enforce the practice's schedule, so out-of-hours appointments should not arise.
The ladder, owner-decided:

1. the round-robin-assigned room;
2. on `ABWESENHEIT`, the remaining rooms of `{MA 1, MA 2, MA 3}`;
3. if all three refuse, write it anyway with `ignoreValidation: true` — thevea's own UI does the
   same behind an "are you sure?" confirmation, so this is the sanctioned override, not a bypass.

This lives in `app/sync/orchestrator.py`, which already owns the work-list loop and outcome
classification (ADR-0004 D4). The engine, the connectors and the template are untouched; each
attempt is a **full governed run**, so the idempotency precondition is re-evaluated and a retry
cannot produce a duplicate. It also fixes a latent flaw: the current round-robin assigns rooms
blindly, with no knowledge of availability.

We deliberately do **not** read thevea's working hours to pre-filter. thevea owns that schedule;
copying it into our code would create a second source of truth that drifts on the first holiday,
vacation or hours change (CLAUDE.md §0). Let thevea refuse, and classify the refusal.

### D7 — A forced write is recorded, never silent
`ImportReport` gains a `forced` list alongside `created`/`skipped`/`failed`/`excluded`, and the
completeness identity becomes `total == created + forced + skipped + failed`. The appointment also
carries a visible marker in its `bemerkung`. Rationale: an override nobody can see is
indistinguishable from a bug, and this platform's entire pitch is that consequential actions are
visible before and after the fact.

Considered and not built: gating the forced write behind the engine's `ApprovalGate`. The owner
wants the appointment to land so it can be judged in the calendar. If forced writes turn out to be
common rather than rare, the gate is the upgrade path and the seam already exists.

### D8 — `calendar_import` v3 (versions are immutable)
Adding a step and a state binding is a new template version, not an edit (ADR-0002 #15); the seed
skips an existing `(name, version)`. **Operational consequence:** instances pin the version they
deployed on, so already-deployed v2 instances keep writing patient-less appointments until they are
redeployed on v3. That is the intended safety property, but it must be said out loud rather than
discovered.

The publish gate (`app/templates/validation.py`) already accepts this shape: `ensure_patient` acts,
so it needs an allowlist containing its executed tool and at least one postcondition — it has both.

### D9 — GDPR posture: more data at the destination, none more at ours
The card is patient master data, but it lands in the **practice's own thevea account** — the same
controller, the same system the data is already used in. Our side persists nothing new:
`ImportJob` keeps refs only (ADR-0004 D4a), and the episode log keeps check results and reasons,
never patient content (ADR-0002 #11, ADR-0003 D7). One genuinely new exposure: matching **reads
other patients' records** from `patientUebersicht`. Those rows are held in memory for the duration
of the comparison and must never be logged, traced or persisted.

## Consequences

- **Changed seams:** `DestinationCalendar` gains `find_patient`/`create_patient`;
  `create_appointment` becomes patient-bound. `SourceCalendar` is untouched, so both source
  connectors (healthyfeet, doctolib) are unaffected.
- **New modules/objects:** a `DestinationPatientProvider` (`app/providers/appointment.py`), a
  `create_patient` tool (`app/workloads/import_tools.py`), patient operations on
  `TheveaConnector`, the room ladder + `forced` bucket in the orchestrator, `calendar_import` v3.
- **No engine change, no schema migration.** `ImportJob.forced` is the one persisted addition and
  is a JSONB list alongside the existing four — same shape, so it rides the existing migration
  pattern. Everything else is data over existing seams; this is again the test that ADR-0003/0004's
  abstractions were right.
- **Frontend:** the import panel gains a `forced` category with an explanation of what the operator
  must do about it.
- **Behaviour change for the practice:** imported appointments stop being room notes and become
  real patient appointments. Historic v2-imported appointments are *not* migrated — they stay as
  `SonstigerTermin` room entries. Backfilling them would require updating or deleting existing
  appointments, which D1 structurally forbids.
- **Cost:** an import now performs a patient search per appointment on top of the existing reads.
  `find_appointment` already re-reads the whole window per appointment; if migrations grow, both
  belong in the same future caching pass — out of scope here (CLAUDE.md §III).

## Deliberately not building

Updating or merging existing patient cards · automatic duplicate removal (`patientEntfernen` stays
unwrapped; cleanup is manual, per owner) · reading thevea's working hours to pre-filter (D6) ·
occupancy/overlap checks (thevea allows overlap; the owner accepts it) · Verordnung/Heilmittel
integration for the procedure name (D5) · insurance fields on the card (not needed for this
practice's Rechnung) · an approval gate on forced writes (D7) · batch creation via
`addPatientenTermine` (single-appointment governance is the contract; batching would break
ADR-0004 D3) · backfilling v2-imported appointments.

## Open questions

- **Does the Rechnung form pull everything from the card?** The owner's field list is complete for
  the card, but whether thevea's invoice flow asks for anything further has not been checked. If it
  does, the time saving that motivates this ADR shrinks. Owner to confirm.
- **Does a sentinel date of birth (D4) print on a Rechnung?** If thevea puts the date on the
  invoice, `01.01.1911` reaches a document the patient sees. The card's `bemerkung` marker makes
  such cards findable, but what to do about them is the owner's call.
- **`patientUebersicht(search:)` semantics** — whether it matches dates of birth as well as names,
  and how it pages, decides whether searching by surname alone is sufficient for matching (D3).
- **The address `land` field** is an `Int` of unknown domain (ISO-3166 numeric, or an internal id).
  It is optional and therefore omitted; if country ever matters, it needs one capture to resolve.
