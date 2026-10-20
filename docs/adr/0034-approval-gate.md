# ADR-0034 — The approval gate: a second administrator approves exactly what is sent

Status: accepted · 20 October 2026 · built (see Implementation status) · step 5 of ADR-0028's
build order · realises ADR-0022 D4's "real approval gate" for workflow cases · builds on
ADR-0024 (mail), ADR-0030 (cases), ADR-0033 (letters), ADR-0031 (agents chosen by kind)

## Context

Sending a tenant a Betriebskostenabrechnung is the first action in the real-estate workflow that
leaves the platform and cannot be taken back. Until now the platform had no approval anywhere:
`/approvals` returned `[]`, the engine was wired with `AutoApprovalGate`, and the run-scoped
`approval` table was never used (ADR-0022, Context). Wonderful's approval node, read on
19 October 2026, lets whoever may start a run approve it. That is the weakness to avoid.

## Success criterion

1. Nothing is sent until **someone other than the requester, who is a workspace administrator**,
   approves it. The server checks this, not the page.
2. What is approved is **exactly** what is sent: change a figure, a letter or an address after
   the request, and the approval no longer applies.
3. Every letter's delivery is recorded. A letter the mail server refused can be sent again, and
   one that was sent never is.

## Decision

### D1 — An approval request is bound to a fingerprint of what it approves

`approval_request` (tenant, case, action `send_letters`, status, `fingerprint`, `payload`,
`requested_by`, `expires_at`, `decided_by`, `decided_at`, `note`, `deliveries`).
- The fingerprint is a SHA-256 of the input revision, the result, every letter (with its
  enclosure) and the payload: the recipients and their addresses.
- Saving the input, or reading the documents again, withdraws a pending request.
- Approving recomputes the fingerprint against the case as it is now (the precondition) and
  withdraws the request if it differs.

### D2 — Separation of duties, from the signed token

`current_principal` reads the user and their organization role from the same Clerk token as
`current_tenant`. The requester never decides (403). Only an administrator (`org:admin`)
decides (403 otherwise). A rejection needs a reason (422). A request expires after seven days.
Outside production, a request without a token is a local administrator, as `current_tenant`
falls back to the default tenant.

**Consequence:** a workspace with one person cannot send letters, by design. If a one-person
office needs it, the answer is an explicit, recorded self-approval setting that a second
administrator turns on, not a quiet bypass. It is not built.

### D3 — Approving sends, letter by letter, and records each outcome

Approval sends each letter through the workspace's mail transport (ADR-0024):
- one email per tenant, from the agent's sender name;
- the letter as the body, and the enclosed statement (ADR-0033) attached as a text file;
- each delivery recorded as `sent` or `failed`, with the mail server's error (the postcondition,
  per letter).

`POST /approvals/{id}/send` retries the failed letters only. No request is accepted while the
workspace cannot send mail, so nobody approves something that cannot go out.

### D4 — A corrected settlement is sent after its own approval; the same letters never twice

Requesting approval for letters with the fingerprint of an approved request is refused. A
corrected case has a new fingerprint, so its letters can be put up again. The page shows whether
the latest approval describes the case as it is now (`current`).

### D5 — Not through the engine yet

The gate's loop (precondition, a person's decision, execution, a postcondition per letter, a
trace in the case history) lives in `app/approvals/service.py`, not in the laufwise engine. The
engine's `ApprovalGate` is synchronous within a run. Wiring a persisted, resumable gate into it
(ADR-0022 D4, "resume by re-execution") is still open; this gate is the shape it should take.

### D6 — What red flag A2 asked of this step

`docs/RED_FLAGS_2026-10-19.md` A2 put "publish and pin workflow-agent settings" with the approval
gate. The approval's fingerprint covers the result and the letters, and every computation already
keeps the exact input and settings it used (C4). So what is approved can be traced to the
settings behind it. Versioned, published agent settings, as conversational agents have, are still
not built.

## Not building

- Self-approval for one-person offices (D2).
- Letterheads, PDFs, postal delivery, or proof of Zugang (§ 556 Abs. 3 BGB): email is Textform,
  but whether it reached the tenant is the landlord's to prove.
- Approvals for other actions; `action` is the extension point.
- Removing the unused run-scoped `approval` table.

## Implementation status (2026-10-20)

- Kernel: `app/approvals/service.py` (`fingerprint`, `request`, `decide`, `send`,
  `withdraw_pending`, `Refused`); `ApprovalRequest` (migration `e58b2d7f1a3c`); `current_principal`
  and `Principal` (`app/api/deps.py`); `POST /cases/{id}/approval`; `GET /approvals`,
  `POST /approvals/{id}/decide`, `POST /approvals/{id}/send`; the case detail's `approval` view
  with `can_decide` and `current`.
- Module: the enclosed statement in each letter (`_enclosure`); the calculator's texts print
  amounts in German, so the amount check covers them.
- Studio: "Send the letters" on a case (an address per tenant, request, approve or reject with a
  note, deliveries, send again); "Waiting for your approval" on the Agents page.
- Tests: `tests/test_approvals.py` (11 cases against Postgres, with mail captured): separation of
  duties, required addresses, no mail no request, rejection with a reason, withdrawal on change,
  expiry, partial failure and retry without duplicates, isolation between workspaces, and a
  corrected settlement after sending.
- Checked in the browser against a local SMTP sink: the requester cannot approve; another
  administrator approves; two mails arrive, each to its tenant, with the statement attached.
