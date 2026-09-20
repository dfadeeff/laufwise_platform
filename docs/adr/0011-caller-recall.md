# ADR-0011 — Caller recall: remembering the key, never the content

Status: accepted · 20 September 2026 · supersedes the open question in ADR-0007 §"What verifies a
caller?" and narrows ADR-0007 D3 for one named case.

## Context

Every call starts cold. A patient who rang last week spells their name again, gives their date of
birth again, and waits ~2s while thevea is asked a question it was asked five days ago
(`thevea_calendar.py:89`). The agent's own prompt says it plainly: *"You know nothing about any
caller except what they tell you in this call"* (`prompts/base.md:41`).

The obvious fix — remember the caller and what they have booked — collides with two rules this
platform has been careful about:

- **ADR-0002 #11 (non-negotiable):** calendar *content* is personal data, read transiently and
  **never persisted**; the episode log records check results, never raw calendar content.
- **ADR-0007 D3:** an appointment may only be disclosed when `caller.identity_verified == true`.
  ADR-0007 also notes that caller ID is spoofable and shared inside a family, and recommends
  caller ID as a lookup *hint* with date of birth as the check.

## Decisions

### D1 — Memory stores the key; the calendar stores the content

`caller_memory` holds a **pointer and a salutation**: a hashed caller key, the PVS `patient_id`, a
surname, a locale, the previous call's outcome constant, and when a date-of-birth check last
passed. It holds **no appointment** — no start, no type, no reference, no resource.

When the agent says *"I see your appointment on Tuesday at 10"*, that sentence comes from a **live
`appointments_for()` read at call setup**, transiently, discarded when the call ends. So ADR-0002
#11 is satisfied **unamended**, and — the part worth more than the compliance argument — a
remembered appointment **cannot be stale**, because no appointment is ever remembered.

### D2 — Recall is a published, per-agent policy with a strict default

`recall_policy` is part of the agent contract, so it is versioned, snapshotted and visible in the
revision history:

| Policy | Behaviour |
|---|---|
| `off` (default) | nothing recalled, **nothing written**. Every agent published before this ADR means this. |
| `greeting` | greet a returning caller by surname; pre-load the patient id as a lookup hint. **No appointment fact.** ADR-0007 D3 holds unamended. |
| `full` | additionally state the next appointment's start at greeting. |

### D3 — `full` is a deliberate narrowing of ADR-0007 D3, and is named as one

D3 governs the `read_appointments` enforced step, and that step's precondition is untouched. But
from the caller's seat, hearing their next appointment is the same disclosure however it was
produced, so this ADR does not hide behind the distinction: **under `full`, one fact — the start of
the caller's next appointment — may be spoken to a caller identified only by the number they rang
from.** Anyone reading D3 as "no appointment fact reaches an unverified caller" should read this as
an amendment to D3, and it is intended as one.

It is available only where a practice has switched it on **and acknowledged it** (D5).

### D4 — Recall unlocks nothing

`_identity["verified"]` is never set by memory. `hint_identity()` pre-fills the patient id and
nothing else, which is enforced by the method's signature rather than by a convention. Cancelling,
moving, and the change-notice path all keep their engine preconditions, so **a recalled caller who
asks to cancel is still refused until a date-of-birth check passes**. Under `full`, recall buys one
sentence and zero writes.

### D5 — The binding is only ever minted by a real check

A row is written only when the call actually established identity — `_identity["verified"]` set by
the verified appointment path, or a unique `find_patient` match against a spoken date of birth.
Never from an ambiguous match. The exposure is therefore "someone using a number a **verified**
patient called from", not "anyone spoofing any number".

Publishing an agent with `recall_policy = "full"` requires `recall_acknowledged = true`, checked at
the publish gate. An unacknowledged agent cannot be published and therefore cannot answer a phone.
The acknowledgement is stored in the revision, so an audit shows who accepted it and when.

### D6 — Bounded, erasable, and never in the way of a call

180-day retention on the existing daily sweep; an explicit "forget everyone this agent remembers"
endpoint for Article 17. The recall read happens in the Twilio webhook behind a 2.5s timeout: on a
miss, a timeout or a `StateUnavailable`, recall is simply absent and the call proceeds exactly as it
does today. **Memory never delays or fails a call.**

## What the practice is accepting under `full`

- Phone numbers are shared inside families, inherited, and reassigned by carriers. Anyone holding
  the number hears the patient's surname and next appointment time with no check performed.
- That is a disclosure of health-adjacent personal data to an unauthenticated party (Art. 5(1)(f),
  Art. 32), and at scale a reportable incident.
- The practice must update its privacy notice (caller ID processed and stored as a pseudonym, 180
  days) and its Verzeichnis von Verarbeitungstätigkeiten.

`greeting` carries none of this and is the policy a practice should run unless it has a reason.

## Consequences

- One new table, tenant- and agent-scoped, unique on `(tenant_id, agent_id, caller_hash)`. Memory
  never crosses agents, so a practice running two agents does not silently pool their callers.
- The caller key is `sha256(pepper + tenant_id + E.164)`. Without `CALLER_MEMORY_PEPPER` configured,
  recall is **disabled process-wide** — fail closed, and the table alone is never a phone list.
- A pre-existing exposure this ADR does **not** close: `telephony.py` already persists the raw
  caller number in `conversation.metadata_["from"]`, and the retention sweep deletes events while
  keeping the conversation row. That is larger and longer-lived than this table, and deserves its
  own change; it should not be presented as fixed by this one.
