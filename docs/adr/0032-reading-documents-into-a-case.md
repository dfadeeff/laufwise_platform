# ADR-0032 — Reading documents into a case: a model fills the form, the documents confirm it, a person signs it off

Status: accepted · 19 October 2026 · built (see Implementation status) · first half of step 4 of
ADR-0028's build order (the letters are the second half) · builds on ADR-0029 (files) and ADR-0030
(workflow agents and cases)

## Context

A case's input had to be typed or pasted. For a Betriebskostenabrechnung that is the slow, error-
prone part: 15 to 18 cost rows, tenancies, heating shares. The documents are already attached to
the case. A model can read them, but a model's reading must never become a figure in a tenant's
settlement on its own.

## Success criterion

1. A person attaches the documents and gets the input filled in, on the form they already know.
2. Every amount, share and date the model read is looked up in the documents' own text, and what
   is not there is shown to the person.
3. **A model's reading is never computed before a person has confirmed it.** The kernel enforces
   this, not the page.
4. Measured: the two test cases read identically to their hand-checked inputs, run after run.

## Decision

### D1 — The model fills the workflow's form, strictly, and does nothing else

The kernel turns the workflow's form schema (ADR-0030 D3) into a strict structured-output schema
(`app/extraction/strict.py`):
- every field is required and no other field is allowed, so an absent fact is an explicit null,
  never an omission or an invention;
- decimals and dates stay strings with a pattern, so no float is involved;
- an enum allows null ("unsure").

The model gets the PDF itself, not our text of it. Its instructions are a short kernel rule plus
the workflow's `extraction_guide` (module data): copy exactly, convert notation only, never
compute, estimate or complete. Allocability, splits and sums are never asked of it; the module's
`compute` does those.

### D2 — The documents confirm what can be confirmed

After reading, every decimal and date in the input is looked up in the documents' text
(`app/extraction/provenance.py`). A German document prints them as 1.250,00, 68 %, 50/50 or
01.08.2025, and all of those forms are recognised. Whatever is not found is listed on the case
(`to_check`) and shown first to the person checking. A match is not proof the value is in the right
field, and the page says so. A scan or an image has no text layer, so everything read from it is
listed.

### D3 — A person confirms; until then the input is not computed

`workflow_case.input_source` is `extracted` after a reading and `person` after any save. Computing
an extracted input is refused (409). Saving, with or without corrections, is the confirmation; it
clears `to_check`. The reading is in the case's history with the model that made it and the
documents it read. Reading again replaces the input, after the page asks.

### D4 — One provider seam, OpenAI today

`DocumentReader.read(schema, guide, documents) -> dict` (`app/extraction/reader.py`). The one
implementation calls OpenAI's Responses API over the `httpx` already in use: no new dependency. The
default model is `gpt-5.5` (`EXTRACTION_MODEL`), separate from the voice model because reading
wants accuracy and a call wants speed. Another provider is another class behind the same protocol.

### D5 — A fact is held as the documents state it; the calculator derives the rest

The first live evaluation found that the Betriebskosten `Tenancy` asked for the part of a tenancy
inside the billing period. That is a calculation, not a fact in any document. Readings varied:
one run gave Mietbeginn 2021-03-01, another an empty end. A tenancy now holds its dates **as
stated**: a start in 2021 stays 2021, and an unstated start or end is null. The calculator clips
each tenancy to the period, and computes an unstated start as the period start, **saying so** in
the assumptions. The rule for every future workflow: **the input holds what the documents say;
the module computes everything else.** A field that needs judgment to fill is a field the module
should derive.

## Measured

`backend/scripts/eval_extraction.py` reads each test case's PDF, compares field by field with the
hand-checked fixture, lists what could not be confirmed, and computes. Free text (labels, sources,
names) is not compared. With `gpt-5.5`, 19 October 2026:

| | Before D5 | After D5 |
|---|---|---|
| Readings identical to the fixture | 3 of 6 | **6 of 6** |
| Values not found in the documents' text | 0 | 0 |
| Balances computed from the reading | wrong or blocked when the dates differed | NK-01 171.40; NK-02 42.61 / 139.08 / −196.39, every run |

Two clean test documents are not a customer's documents. The number that matters is this script
run over the customer's real Hausgeldabrechnungen, from several Verwalter.

## Not building

- Reading scans (OCR): the model reads images, but provenance cannot confirm anything in them.
  Wait for a customer who sends scans.
- Auto-confirming a reading that has nothing to check. A person signs off every reading.
- A second provider, until there is a reason (cost, accuracy on real documents, data residency).
- Sending documents to a model outside the EU is a data-protection decision for each customer. The
  documents name tenants. Before a real customer's documents are read, settle the provider's data
  processing terms and region.

## Consequences

- A Betriebskostenabrechnung goes from PDFs to a checked result in about a minute of a person's
  time: read, check, confirm, compute.
- `OPENAI_API_KEY` on the backend enables reading; without it the button answers 503 and nothing
  changes.
- Cases saved before this change keep working: an input with the old, clipped tenancy dates
  computes the same.

## Implementation status (2026-10-19)

- Kernel: `app/extraction/` (`strict.py`, `provenance.py`, `reader.py`, `text.py`),
  `Workflow.extraction_guide`, `WorkflowCase.input_source` / `to_check` (migration `be2f3a4b5c6d`),
  `service.apply_extraction` and the refusal in `service.compute`, `POST /cases/{id}/extract`,
  setting `EXTRACTION_MODEL`.
- Module: the Betriebskosten guide; `Tenancy.start/end` as stated, clipped in `calculate.py` and,
  independently, in `verify.py` (red flag C7 re-derives the parties and advances from the dates);
  fixtures updated to the documents' own dates.
- Studio: "Read documents into the input", the check banner with the values to check, "Confirm
  input", Compute held until then, the reading in the history.
- Tests: `tests/test_extraction.py` (strict schema, recognising German numbers, shares and dates,
  the reader against a mock transport, the read → refuse → confirm → compute flow against
  Postgres, no provider configured); three new cases in `tests/test_betriebskosten.py`.
- Checked in Chrome end to end with the live model on NK-02: upload, read (about 30 s), compute
  held, confirm, computed result.
