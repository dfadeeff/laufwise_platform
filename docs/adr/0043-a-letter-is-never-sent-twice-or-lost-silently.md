# ADR-0043 — A letter is never sent twice, and never lost silently

Status: accepted · 9 November 2026 · built (see Implementation status) · amends ADR-0035 D3
(how a delivery is recorded) · keeps ADR-0024 (transports, including a workspace's own SMTP
account) unchanged

## Context

ADR-0035 promised that "a letter that was sent is never sent twice". Reading the send path on
9 November 2026 shows three ways it can be:

1. **A crash in the middle of sending.** `approvals.send` mails every letter, then writes all
   outcomes to `approval_request.deliveries` in one commit at the end. If the process dies or a
   deploy restarts it inside the loop, letters already accepted by the mail server are recorded
   as nothing, and "send again" mails them a second time.
2. **Two "send again" requests at once.** `POST /approvals/{id}/send` reads the approval before it
   locks the case. The second request waits for the lock, then works from the copy it read
   before the first committed, and sends the same letters again.
3. **A lost answer.** A timeout or a dropped connection after the mail server took the message is
   recorded as `failed`, and a failed letter is offered for sending again.

The selling point is a verifiable outcome. A tenant who gets the same settlement twice, or a
landlord who cannot tell whether it went out, breaks it on the first day.

## Success criterion

1. A crash, a deploy or a double click at any moment never sends a letter twice automatically.
2. No letter that may have been sent is shown as unsent or offered for an ordinary retry.
3. An outcome the platform cannot know is shown as **uncertain**, and an administrator resolves it
   in the Studio with a recorded reason.
4. Each of these is proven by a test that fails on today's code.

## Decision

### D1 — One row per delivery, and the database refuses a second live one

A new table `letter_delivery`: `id` (the operation id, also the Message-ID, D6), `tenant_id`,
`approval_id`, `recipient`, `email`, `title`, `status`, `error`, `resolved_by`, `resolved_at`,
`note`, `created_at`, `updated_at`.

Status: `sending` · `sent` · `failed` (the mail server refused it; nothing went out) ·
`uncertain` (it may have gone out) · `not_sent` (an administrator confirmed it did not arrive).

A partial unique index allows **one live row** (`sending`, `sent` or `uncertain`) per approval and
recipient. A second send of the same letter cannot be written, whatever the code above it does.
`failed` and `not_sent` rows stay as history; a new attempt is a new row.

### D2 — The intent is committed before the mail server is contacted

Sending locks the case row, reads the approval again under that lock, and reads its deliveries.
Then, one letter at a time: the message is built, a `sending` row is written and **committed**,
and only then is the mail server contacted; the outcome is committed as soon as it is known. A
request that arrives while another is sending finds the live rows and sends nothing for them; if
both try to write the same letter's intent, the index (D1) refuses the second. Because the case
lock ends at the first commit, D8 keeps the case from changing meanwhile.

Approving still sends at once, inside the request (chosen over a background worker on
9 November 2026: the administrator sees the result, and the change stays small).

### D3 — What the mail server said decides `failed` or `uncertain`

| What happened | Status |
|---|---|
| Could not connect, TLS or login refused, sender or recipient refused, message refused with an SMTP error code | `failed`: nothing went out; it can be sent again |
| The server accepted the message (`send_message` returned) | `sent`, even if closing the connection fails afterwards |
| Timeout, dropped connection or any other error while the message was being transferred | `uncertain` |

A timeout is not evidence that nothing was sent (runtime contracts proposal, §4).

### D4 — A row left in `sending` becomes `uncertain`

A `sending` row older than thirty minutes belongs to a process that died. The SMTP timeout of
twenty seconds is per socket operation and a send makes about fifteen, so a slow server can take
minutes; thirty is far beyond that. The next send of that approval, and the case view, treat it
as `uncertain`; the send path writes the change. No background task is added.

Every write of an outcome, a stale mark or a resolution changes the row only from the status it
was read in. A request that answers after its row was marked uncertain or resolved does not
overwrite that; its answer is recorded in the case history (`late_delivery_outcome`).

### D5 — An administrator resolves an uncertain letter, with a reason

`POST /approvals/{id}/deliveries/{delivery_id}/resolve` with `outcome` and a required `note`:

- `sent`: the administrator confirmed it arrived (the tenant has it, or it is in the sent folder
  or the provider's log). The row becomes `sent`.
- `not_sent`: the administrator confirmed it did not arrive. The row becomes `not_sent`, and the
  letter can be sent again through the ordinary send.

Only a workspace administrator resolves, and the decision is recorded on the row and in the case
history. The platform never resolves one by itself.

### D6 — A fixed Message-ID per delivery

Each message carries `Message-ID: <{delivery id}@{sending domain}>`. A letter can be found in the
workspace's sent folder or in the provider's log by that id, which is the evidence an
administrator uses in D5. Automatic reconciliation against a provider's API is not part of this
decision.

### D7 — The old `deliveries` column is copied, then no longer written

The migration copies each `approval_request.deliveries` entry into `letter_delivery` (`sent` and
`failed` as they are). The code reads and writes only the table. The column stays until a later
migration drops it, so this change can be rolled back: the downgrade writes each letter's latest
attempt back into the column first, with anything that went or may have gone out as `sent`
(the old code resends only `failed`).

### D8 — A case does not change while one of its letters may have gone out

Saving the input, reading documents into it, entering an awaited fact and taking a decision are
refused (409) while any letter of the case is `sending` or `uncertain`. Otherwise the approval
would stop describing the case, the uncertain letter would drop out of view, and a resumed run
would offer the letter again under a new approval, which the index (D1, per approval) does not
stop. An administrator resolves the letter; then the case can change.

## Consequences

- ADR-0035's promise holds under crashes and concurrency, enforced by the database, not only by
  the code path.
- An administrator sometimes has a decision to make that the platform used to guess. That is the
  intended trade: an honest "we don't know" over a silent duplicate or a silent loss.
- Approved letters whose process died **before** the intent was committed have no live row; they
  appear unsent and are sent by the ordinary "send again". Nothing was contacted, so this is safe.

## Not building

- A background worker or outbox for letters (D2): a later step, when an action must go out
  without anyone clicking.
- A transactional email API with idempotency keys: ADR-0024 D3 lets a workspace send through its
  own SMTP account, which has none. Possible later for the platform transport only.
- Automatic reconciliation through a provider's API or delivery webhooks (D6).
- Resolving uncertainty for other actions; `letter_delivery` is specific to letters until a
  second consequential action needs the same shape.

## Proof

Tests against Postgres, with the mail transport replaced by controllable fakes:

- a crash after the first letter of two is accepted: the first is `sent` or `uncertain`, never
  sent again by "send again"; the second is sent once;
- two concurrent sends of one approval: every letter is handed to the mail server once;
- a timeout during transfer: `uncertain`, and "send again" does not resend it;
- a refusal before transfer: `failed`, and "send again" sends it;
- a `sending` row older than thirty minutes: shown and treated as `uncertain`;
- a late answer from the mail server does not overwrite a resolution;
- a letter that cannot be rendered is never left `sending`;
- two decisions at once decide once;
- a case, and a waiting case's awaited fact, cannot change while a letter is uncertain;
- rolling the migration back keeps what was sent;
- resolving: `sent` and `not_sent` with a note, by an administrator only, recorded in the case
  history; `not_sent` then allows exactly one new send;
- the database refuses a second live row for the same approval and recipient;
- the migration copies existing deliveries.

## Implementation status (2026-11-09)

- Kernel: `LetterDelivery` and `LIVE_DELIVERY` (`app/db/models.py`, migration `c1d6e7f8a9b0`,
  which copies the old `deliveries` column); `repo.letter_deliveries`, `repo.get_letter_delivery`;
  `approvals.send` (message built, then intent per letter in a savepoint, committed before the
  mail server, outcome committed at once), `approvals.resolve`, `approvals.status_of`,
  `approvals.latest`, `approvals.ensure_settled` (D8); `repo.update_delivery_if` (every status
  change from the status read); the approval re-read under the case lock;
  `mail.deliver` raises `MaybeSent` during the handover and no longer raises once the message was
  taken. Call summaries judge a `MaybeSent` by its cause, so their retries are unchanged.
- HTTP: `POST /approvals/{id}/deliveries/{delivery_id}/resolve`; the case detail's `deliveries`
  are each letter's latest attempt, with its id.
- Studio: every approved letter is listed with its state; an uncertain one asks how the
  administrator knows and offers "It arrived" / "It did not arrive"; "Send the unsent letters"
  covers failed, not-arrived and never-tried letters.
- Tests: `tests/test_letter_delivery.py` (the crash, the dead process, two sends at once, the
  timeout, the Message-ID, resolving, isolation, the database rule, the migration, and the mail
  server's answers); the crash and concurrency tests sent letters twice before the change.
