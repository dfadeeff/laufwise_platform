# ADR-0015 — A practice claims its phone number from a pool

Status: accepted · 3 October 2026 · extends ADR-0007 (inbound voice agent)

## Context

A practice could build and publish an agent itself, but could not give it a phone line. An
administrator mapped each number to a tenant in the `VOICE_NUMBER_ASSIGNMENTS` environment variable
(a redeploy per practice), and someone pointed the number's webhook at `/telephony/incoming` in the
Twilio console by hand.

Three ways a practice could get a number were weighed:

| | How | Why not (for now) |
|---|---|---|
| **Buy in the Studio** | search and purchase from the platform account | German local numbers need a regulatory bundle (address and ID review, days) per number owner, so purchase is not instant without a document flow |
| **Bring their own Twilio** | the practice connects its Twilio account | webhooks, signature checks, hang-up and transfer would all become per-tenant |
| **Claim from a pool** | the operator buys numbers under one bundle; a practice claims one | chosen |

## Decision

### D1 — Ownership is a row, not an environment variable

`phone_number(number PK, tenant_id, twilio_sid, claimed_at)`. The number is the primary key, so two
practices racing for one cannot both win. Owning a number and answering it stay separate: the
practice owns it here, and an agent's `VoiceChannel` answers it. Numbers in
`VOICE_NUMBER_ASSIGNMENTS` keep working and count as owned by their practice, so nothing in
production breaks.

### D2 — The pool is the platform account minus what is owned or used elsewhere

A number is offered when it is voice-capable, no practice owns it (row or env), and its Twilio
Voice URL is empty or already ours. A number pointed anywhere else is somebody's live line and is
never offered. No pool table: the Twilio account is the stock.

### D3 — The platform wires the number; the practice forwards its line

Claiming a number sets its Voice URL to this deployment's `/api/v1/telephony/incoming`, and
activation sets it again, so the console step is gone and a hand-edited webhook is repaired.
Callers keep dialling the practice's usual number: the Studio shows how to forward it (unanswered
calls recommended, so staff still pick up first).

### D4 — Limits that protect the pool and the line

A practice holds at most three numbers. A number an agent's channel holds, live or paused, cannot
be released, so callers never hear "this number is not available" because someone tidied up.

## Consequences

- A practice goes from sign-up to a live phone line without the operator, as long as the pool has
  stock. The operator's job is buying numbers and leaving their Voice URL empty (DEPLOY.md §1c).
- All numbers are in one Twilio account, billed to the operator. Per-practice billing would need
  Twilio subaccounts (not built).
- Forwarding depends on the practice's carrier. The Studio's GSM codes cover most mobile and many
  landline connections; phone systems use their own setting.

## Open questions

- Restocking: alert the operator when the pool runs low, or buy automatically where Twilio allows.
- Subaccounts per practice, if billing has to follow the practice.
