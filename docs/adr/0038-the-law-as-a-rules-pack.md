# ADR-0038 — The law a workflow applies is a rules pack: written by the platform, proven by an expert's cases, never by the customer

Status: proposed · 1 November 2026 · not built · builds on ADR-0028 (modules), ADR-0030 (workflow
agents and cases), ADR-0033 (reading documents), ADR-0036 (a second workflow)

## Context

The property-management workflows apply German law: what may be charged under the BetrKV, how
heating is split under § 9b HeizkostenV, the deadline of § 556 Abs. 3 BGB, the limits of § 558
BGB. That law lives today in one `catalogue.yaml` per workflow. It is marked `reviewed: false`,
and nobody has said who reviews it, how it changes, or how a customer can trust it.

The test series in `docs/use_cases/real_estate/bo/` (NK-01 to NK-04, and the review of the first
two) shows what is at stake:

- **The model knew the norm and did not apply it** (review, finding 1). An agent that holds the
  law in its head lets caution beat the norm. A rule the engine executes cannot be outweighed.
- **The expected results were written from the agent's own output** (finding 3). The error moved
  into the test, and the test could not fail it.
- **Some rules are choices only an expert can make.** On NK-02 the engine and the expert's
  corrected sheet differ by 15.86 € for the first tenant. Both readings are lawful: days or
  months for the time split; whether a lease's general reference to § 2 BetrKV covers the
  maintenance of smoke detectors.
- **The law has dates and places.** The cable-TV rule ends on 30.06.2024 (NK-01). The 15 %
  rent-increase cap applies only where a state regulation says so (NK-03). A 2024 settlement
  follows 2024 rules (NK-04).

The platform is for small businesses. A small property manager will not write rules, read YAML
or maintain a legal catalogue. A legal expert will not either: what an expert produces is a task,
a worked solution and review notes, in German, as documents.

## Success criterion

1. A customer never writes a rule. They state facts, take a few standing decisions once, and
   answer the questions only a person can answer.
2. An expert never writes a rule file either. They hand over cases and solutions in their own
   format. A coding model turns them into rules, tests and code, and the expert reviews the
   result in plain language.
3. Every figure a tenant receives can be traced to a rule, its source in law, the version it
   came from, and who reviewed that version.
4. No expected result in a test was derived from the platform's or a model's output.

## Decision

### D1 — Four kinds of knowledge, four owners

| Layer | Example | Owner | Changes |
|---|---|---|---|
| **The law** | BetrKV categories, § 9b HeizkostenV, § 556 deadlines, BGH rulings | the platform, proven by an expert | rarely, on a known date |
| **Regional data** | a state's cap regulation, degree-day table | the platform | yearly, per edition |
| **Standing decisions** | split by days or months, payment term, sender | the customer, once, in the settings form | rarely |
| **Case facts** | amounts, dates and lease clauses of this case | read by the model, confirmed by the customer (ADR-0033) | every case |

Precedence runs from the law down to the case. The result shows which layer each figure came
from. A case-specific document that carries regional data (a Mietspiegel extract) stays a case
fact.

### D2 — A workflow's law is a rules pack, separate from its procedure code

The rules pack is what `catalogue.yaml` is today, made explicit:

- **Versioned and immutable once published.** Changing a rule means a new version. Every
  computation already records `rules_version` (ADR-0030), so any letter traces to its rules.
- **Dated.** A rule carries `valid_from` and `valid_until`. The engine takes the rule valid for
  the billing period, and asks when a period spans a change, as it does for cable TV today.
- **Cited.** Every rule names its source: paragraph, ruling, or the expert's decision with its
  reason.
- **Reviewed.** The pack records who reviewed which version and when. `reviewed: false` stays
  visible to the customer (D6).

It is a block of an agent's definition (ADR-0039 D2): stored as immutable versions, exported as
a file for review. It has no rule editor of its own (Not building); the builder writes it into a
draft, and a person publishes it. An agent's functions change when it meets a new kind of case.
Its pack changes when the law changes, or when an expert corrects a rule.

### D3 — An expert supplies cases, not rules

The expert's native output is what the test series already is:

1. **A task** (Auftrag): the documents of one case and what must be produced.
2. **A solution sheet** (Lösungsblatt): the figures, decisions and citations the expert holds
   correct, written before seeing any output.
3. **Review notes** after a run: where the output is wrong and which norm says so.

The expert hands these documents to a coding model, as they are. Nobody transcribes them. The
model (the builder, ADR-0039 D6) works out everything else, in one draft:

- the task becomes a fixture, written as extraction should emit it;
- the solution sheet becomes an expected-results file;
- the review notes and the norms they cite become rule changes in the pack, each with its
  citation;
- new procedure code, when the case brings a new kind of fact or procedure (§ 558, deadline
  analysis).

The model works the law out **once, at build time**, into rules that then run without a model.
It never works the law out at run time, case by case. That is the agent the test series
measured, and it let caution beat the norm (Context).

The model's work is proven, not trusted:

- **Tests:** the case passes against the expected results, and every earlier case still does.
- **Transcription:** each expected figure quotes the sheet it came from, with page and line. A
  misread figure shows up as a disagreement the expert sees, not as a silently passing test.
- **Disagreements:** where the model believes the sheet is wrong, it does not change the
  expected figure. It reports the disagreement with its citation.

The expert reviews in their own language, never in YAML:

- the rendered settlement next to their solution sheet;
- the rules page (D6), which lists every rule with its source and effect.

Where the expert's sheet and the norm disagree (the first NK-02 sheet gave the whole July-December
consumption to the new tenant, against § 9b Abs. 3), the disagreement is recorded with both
positions and decided before the expected result changes.

### D4 — Test independence is a process rule, recorded in the file

Order: task to the platform → run → output to the expert → expert's solution sheet → expected
results. The expert keeps the sheet until the run is done.

So a new case is built **from the norm, without its answer**:

- The model reads the task and names every norm that governs it.
- It writes each rule generically, with the norm quoted (e.g. the cap base is the rent three
  years before the increase takes effect, without § 559 increases). Never "this case's answer
  is 669".
- Each rule is tested on small inputs taken from the norm's own wording (e.g. received on 20.11.,
  in effect from the start of the third month after, so 01.02.). These tests prove the rule, not
  the case.
- The case is run, and its output is the answer the task asks for. It is stored as an
  unverified snapshot until the sheet arrives.
- Where the law leaves a judgment to a person (D5), the answer shows the consequence of each
  outcome and the reasoned choice, recorded as a person's decision, not hidden in a rule.

Each
expected-results file names its source sheet and date. A file without a source does not count as
proof.

A rule is changed against the norm, never against an expected number. A change that only makes a
number match, with no citation, is refused in review.

Every check against invented figures gets a counter-check against unnecessary non-answers (the
review's NO_ANSWER_WHERE_RULE_EXISTS, MISSING_DEADLINE, CONDITIONAL_WHERE_DEFINITIVE,
LETTER_NOT_DISPATCHABLE). Where such a check is structural (a letter without a dated deadline,
an open item without an addressee and a due date), it runs on every result in `verify` or the
letter check, not only on the fixtures.

### D5 — The customer decides only what needs a person

What the customer contributes, and how:

- **Standing decisions** are fields of the workflow's settings dataclass, shown as a form with
  the consequence of each choice in words. The choices are the lawful ones the pack allows.
- **Case facts** are read by the model and confirmed by the customer (ADR-0033).
- **Legal judgments** are fields a person sets with a reason. Examples: whether a renovation
  clause is valid (ADR-0036 D2), whether the landlord is responsible for a late settlement
  (NK-04). The model leaves them empty, and the case asks until a person answers.

### D6 — The customer sees the law, and cannot change it

- Each figure in a result links to the rule that produced it, and the rule to its source.
- A rules page per workflow lists every rule in plain German: what it does, its source, its
  validity, the pack version and its review.
- An unreviewed pack says so on the page and on every result computed with it. Before a real
  property runs through a workflow, its pack is reviewed.
- A customer who disagrees with a rule cannot override it in their workspace. A disagreement is a
  candidate case for the expert (D3). A per-workspace override would create a second truth that
  nobody reviews.

### D7 — Legal texts are reference, not authority

The full texts (BetrKV, HeizkostenV, the BGB provisions, the key rulings) are what the coding model
reads to write rules (D3). They help the reading model understand documents, and they explain a
citation to the customer. They never decide an outcome. Retrieving a paragraph at run time
does not replace a reviewed rule.

### D8 — The legal-services boundary stays with a professional

The platform computes. A professional (the customer as Hausverwalter or Sondereigentumsverwalter)
approves what is sent (ADR-0035). That boundary is deliberate for the Rechtsdienstleistungsgesetz:
the settlement is the property manager's own work (§ 5 RDG); the platform is the tool.
Outputs that come closer to individual legal advice, such as which claims are still enforceable
after a missed deadline (NK-04), stay behind that approval. **Open: a lawyer confirms this
boundary before the module is marketed.**

## Not building

- A rule editor or a rules DSL. The builder writes rules into a draft; a person reviews and
  publishes it (ADR-0039 D6).
- Per-workspace rule overrides (D6).
- A general regional layer. The one regional rule NK-03 needs (the 15 % cap list) lives in the
  pack with its validity. A layer comes with a second state or a second edition.
- An expert portal or review queue. The expert's documents go to the builder (D3).
- Answers to rule questions an expert has not yet decided. Until decided, they are asked
  per case.

## Consequences

- Building a new case type is mostly fixture plus expected result plus pack rows. New procedure
  code is needed only when a case brings a new kind of fact or a new legal procedure (§ 558,
  deadline analysis).
- The expert relationship is part of the product: "rules reviewed by a specialist, current as of
  a date" is what a small property manager cannot build alone.
- A law change becomes a new pack version on its effective date. Cases computed with the old
  version keep it.
- The two open NK-02 choices (days or months; smoke-detector maintenance under a § 2 reference)
  are decided by the expert and recorded in the pack with that decision as their source, before
  the NK-02 expected results change.

## Implementation steps (when accepted)

1. Pack metadata in `betriebskosten/catalogue.yaml` and `kaution/catalogue.yaml`: version,
   `reviewed_by`, `reviewed_on`. Per rule: `valid_from`, `valid_until`, citation. The loader and
   `rules_version` carry them.
2. Expected-results files beside the fixtures, each naming its source sheet. NK-01 and NK-02
   rewritten from the expert's corrected sheet, after the two choices are decided.
3. The rules page per workflow, rendered from the pack, with the review state.
4. The structural counter-checks of D4 in `verify` and the letter check.
