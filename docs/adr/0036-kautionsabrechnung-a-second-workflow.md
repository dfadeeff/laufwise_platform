# ADR-0036 — The Kautionsabrechnung: a second workflow, as the test of the kernel

Status: accepted · 20 October 2026 · built (see Implementation status) · builds on ADR-0028
(modules), ADR-0030 (workflow agents and cases), ADR-0032–0034 (reading, letters, approvals)

## Context

ADR-0028 D1 drew the cohesion line: workflows that share nouns share a module. ADR-0030 promised
that a module adds a workflow as data types and pure functions, without UI, endpoints or tables.
Both claims were untested with a single workflow. The Kautionsabrechnung (settling a tenant's
security deposit when a tenancy ends) is the next workflow of the same customer. It shares the
module's nouns (tenant, flat, lease), and is the cheapest honest test of both claims.

## Success criterion

1. The Kautionsabrechnung is a workflow of `immobilien`. It is offered, computed, checked, read
   from documents, written as a letter, approved and mailed by the existing kernel.
2. Anything the kernel must change for it is reported here, not absorbed quietly.

## Decision

### D1 — The domain, deterministic like the Betriebskosten

- **Credit:** what the tenant actually paid (§ 551 BGB), plus interest, minus tax withheld, both
  as the deposit account states them. Less paid than agreed: the settlement is over what was
  paid, and says so. More paid than agreed: a question.
- **Claims:** each is looked up in a versioned catalogue (`kaution/catalogue.yaml`,
  `reviewed: false`) and is either offset, retained, or not charged, each with its citation:
  - rent arrears and a settled Nachzahlung are offset;
  - a retention for a Betriebskostenabrechnung not yet done is held back (BGH VIII ZR 71/05);
  - damage is offset only with evidence;
  - cosmetic repairs only under a lease clause a person found valid (BGH VIII ZR 185/14);
  - normal wear (§ 538 BGB) and handling fees are never charged.
- **Result:** the payout, or the claim that remains when the claims exceed the credit, never both.
- **Stated, not hidden:** the § 548 limitation date for damage claims; a deposit above three
  rents (§ 551 Abs. 1); a retention to be settled later.
- **Asked, not guessed:** a claim of unknown kind, a claim or retention without evidence, an
  unassessed renovation clause, no return date, no payment. Questions block the figures.
- **The check is independent** (the rule of red flag C7): `verify` re-derives every treatment
  from the catalogue, and the credit, sums and payout from the deposit.

### D2 — Legal judgment stays with a person

Whether a renovation clause is valid is a legal judgment. `renovation_clause` is set by a
person. The reading guide tells the model to leave it null, and the case asks until someone
decides. The module derives nothing that needs judgment.

### D3 — What the module shares, and what it does not

German money format and the salutation move from `betriebskosten` into `immobilien/letters.py`.
They are this vertical's, not the kernel's. The two workflows share nothing else: separate
catalogues, calculators and checks, so a change to one cannot move the other's figures.

### D4 — The one kernel change it needed

The kernel's letter check counted only amounts the result holds as numbers. A retention's
reason quotes its evidence ("Nachzahlung 2024: 380,00 €"), and the enclosure repeats it, so a
correct letter was refused. `amounts_not_in` now also reads amounts the result prints in its own
text. The rule is unchanged in substance: a letter says nothing the result does not, and the
result is what the person reviewed. That is 13 lines in `app/kernel/drafts.py`, with a test.

Everything else needed nothing: no migration, no route, no Studio page. "New workflow agent"
offers it, the case page renders its forms and result from its dataclasses, and the approval
gate mails its letter.

## Not building

- Calculating the deposit interest from rates and dates: the bank statement states it.
- Partial payouts while a retention is open, as separate approvals. The retention is named in
  the letter and settled with the next Betriebskostenabrechnung.
- Test PDFs and an extraction evaluation: there are none yet. `scripts/eval_extraction.py` is
  Betriebskosten-specific today and becomes per-workflow when Kaution documents exist.

## Implementation status (2026-10-20)

- `app/modules/immobilien/kaution/` (`model.py`, `catalogue.yaml`, `calculate.py`, `verify.py`,
  `workflow.py`); `app/modules/immobilien/letters.py`; registered in `immobilien`'s `workflows`.
- Kernel: `app/kernel/drafts.py` (D4).
- Tests: `tests/test_kaution.py` with two cases written as data (`tests/fixtures/kaution/`):
  - payout, remaining claim, each treatment and citation;
  - questions, parse refusals, the independent check against tampering;
  - the letter and its enclosure;
  - one case through the API from input to a mailed letter.

  Plus a kernel test in `tests/test_letters.py`. Rendered in Chrome at 1280 and 375 px.
