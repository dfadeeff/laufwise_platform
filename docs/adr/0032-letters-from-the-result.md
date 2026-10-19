# ADR-0032 — Letters are written from the result by a template, and checked against it

Status: accepted · 19 October 2026 · built (see Implementation status) · second half of step 4 of
ADR-0028's build order · builds on ADR-0030 (cases) and ADR-0031 (reading documents)

## Context

A Betriebskostenabrechnung ends with a letter to each tenant. The test cases ask for at most five
sentences, with the balance and the deadline to pay or be refunded. The letter carries the
figures a tenant acts on, so a wrong amount in it is the most visible mistake the product can
make.

## Decision

### D1 — A template writes the letters, not a model

Everything a letter must say is already in the result: the period, the tenant's share, the
advances, the balance, and the proposed new advance. A template turns those into German
sentences. The same result always gives the same letter, and a template cannot add a figure, a
promise or a legal claim. Wording that should change is a change to the module's template,
reviewed like code, not an edit to one letter. A model can be added later to vary the tone.
D2's check is what would then keep it honest.

The Betriebskosten letters (`_drafts` in `immobilien/betriebskosten/workflow.py`):
- one per tenant, none for a vacant period;
- the salutation is taken from the name (Herr / Frau / Familie, otherwise "Guten Tag");
- at most five sentences: the period; costs and advances; Nachzahlung with a payment deadline,
  or Guthaben with a refund deadline; for the current tenant only, the proposed new advance under
  § 560 Abs. 4 BGB, worded as a proposal; the enclosures;
- signed with the agent's sender.

Sender and deadline (`payment_days`, default 30) are agent settings.

### D2 — The kernel checks every letter against the result

A workflow may write drafts from a ready result (`Workflow.drafts`). The kernel then checks that
every euro amount printed in each draft (1.032,61 €) is held by the result, ignoring the sign:
a refund prints as a positive amount while the result holds a negative balance. If one amount is
missing, the case is `failed` and **neither the result nor any letter is shown**, the same rule as
the result's own check. Other numbers (§ 560, 30 Tagen, Wohnung Nr. 5) are not amounts and are
not checked.

### D3 — A letter is shown, copied, downloaded; not edited, not sent

The case page lists the letters under the result with Copy and Download. Saving the input clears
them with the result. Sending them, and the approval before it, is step 5.

### D4 — Settings start from the dataclass defaults

The form schema now carries a field's simple default, and a new agent's settings start from it.
`payment_days` is 30, not 0. Agents created before this change compute as if the default were
set.

## Not building

- Editing a letter in place (D3), letterheads, addresses and PDF layout. They come with sending.
- A model drafting or polishing the letters (D1).

## Implementation status (2026-10-19)

- Kernel: `Draft` and `Workflow.drafts` (`app/kernel/workflows.py`); `amounts_not_in`
  (`app/kernel/drafts.py`); drafts written and checked in `service.compute`, cleared on every
  input change; `workflow_case.drafts` (migration `cf3a4b5c6d7e`); schema defaults in
  `app/kernel/schema.py`.
- Module: `_drafts`, `Policy.sender` / `payment_days` and their labels.
- Studio: a Letters section on the case page (Copy, Download); `blank()` uses schema defaults.
- Tests: `tests/test_letters.py`: both test cases' letters (recipients, salutations, amounts,
  deadline, proposal only for the current tenant, at most five sentences), the amount check, the
  defaults, a case carrying its letters, and a letter with a foreign amount hiding everything.
  Checked in Chrome at 1280 and 375 px.
