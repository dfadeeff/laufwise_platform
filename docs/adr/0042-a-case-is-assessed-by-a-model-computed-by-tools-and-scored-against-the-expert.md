# ADR-0042 — A case is assessed by a model, computed by tools, checked by the platform, and scored against the expert's solution

Status: accepted · 8 November 2026 · steps 1 to 3 built (the platform's part), 4 in part; 5 not built

Builds on ADR-0030 (workflow agents), ADR-0038 (the law as a rules pack), ADR-0039 (the builder,
hidden cases), ADR-0041 (reference sources). Changes ADR-0030's "a case workflow makes zero model
calls" for the step this ADR adds, and only for it: the model's output is data the platform checks,
never an action.

## Context

The NK-03 and NK-04 tests (ADR-0039 step 7) compared two agents against the expert's solution
sheet (`docs/use_cases/real_estate/bo/`):

- **Another agent**, which read each task with the law and wrote its answer, scored about 13/14 and
  12/15 by hand.
- **The platform** scored about 13½/14 and 9½/15, and failed two knock-out criteria of NK-04: the
  letter would have claimed 338,00 € from a tenant against whom 242,00 € of it is time-barred.

The difference is where legal judgment sits. In the platform no model runs while a case is
computed: the builder wrote the judgment once, as code (whether a cost is in the lease, whether the
landlord answers for a delay, which exception applies). Right or wrong, it is so for every case,
and a new kind of case needs a new builder run. The other agent judges each case, which is why it
handled NK-04's delay and retroactive tax correctly, and why nothing guarantees it does the same
twice or computes a sum right.

The architecture blueprint in the same folder proposes what both lack: a model that assesses each
case, deterministic tools that compute, a validation gate, and an evaluation harness independent of
the outputs it scores. And the evals so far could not say which agent is better: hidden cases
compare exact figures at field names of one implementation, from hand-written input; they cannot
score the other agent's answer, nor the platform from the task's own documents.

## Success criterion

1. On NK-01 to NK-04, from the task PDFs alone, the platform scores at least what the other agent
   scores on the expert's criteria, with no knock-out criterion failed: NK-03 at least 13/14,
   NK-04 at least 12/15.
2. Every figure a tenant receives is computed by code, never written by a model; every legal
   assessment names its source, quoted from the law texts or the case's documents.
3. It holds on tasks it was not fixed for: NK-05 and later, scored the same way.

## Decision

### D1 — Scoring sheets, not copied figures, measure an agent (step 1)

Each test is a pair: a task (its PDF) and the expert's solution. From the solution, never from an
output, a **scoring sheet** is written: the expert's criteria in his words, each with its points,
whether it fails the solution outright (knock-out), what a correct answer does, and the passage of
the solution it rests on; the threshold at which he calls an answer usable.

Any answer is scored by the same sheet: the platform's (its result and letters, run from the task's
documents through the reader), another agent's (its text), a run from next month. Two kinds of check:

- **Plain checks** where the criterion is a fact a letter or result must or must not state
  (a regular expression over the answer's text): no model involved.
- **A judging model** for the rest. It receives the criterion, the expert's passage and the answer,
  and returns a verdict (met, partly, not) **with a passage quoted from the answer**. A verdict whose
  quote is not found in the answer counts as not met: the judge cannot credit what is not there.

The judge is `gpt-5.5`, the model that also builds (`JUDGE_MODEL`). That it grades work of its own
kind is why every verdict must carry its evidence; a judge from another provider is the stronger
choice once one is configured. The judge is calibrated before it is trusted: on the other agent's
answers it must reproduce the expert's and the reviewer's verdicts (13/14, 12/15; NK-02's old answer
failing on § 9b HeizkostenV and the missing deadlines).

Scoring sheets live with the expert's documents, outside the public repository, like the solutions.
They are never shown to the builder or to the assessing model.

### D2 — A model assesses each case, with its sources quoted (step 2)

A new step kind, `assess`, runs after the input is confirmed and before `compute`. The model
(`gpt-5.5`) reads the case's facts, the definition's law texts (ADR-0041) and the workspace's
documents (the lease), and returns a structured assessment, for each cost and each legal question
the definition names: covered by the lease (yes or no, with the clause quoted), eligible by law (with
the paragraph quoted), who answers for a delay (with the decision cited), which exception applies,
which deadlines run. Each quote must be found word for word in its source, or the step is refused.
A question the sources do not settle becomes a `decide` for a person; one they settle never does
(the expert's NO_ANSWER_WHERE_RULE_EXISTS).

### D3 — Every figure comes from a tool (step 3)

The assessment does not contain amounts it computed. The definition's calculators become tools the
step calls (the settlement, a rent cap, a deadline, what is still claimable), in the sandbox, and the
model may only report what they returned. The letters' existing rule stays: every amount in a letter
is in the result.

### D4 — The gate checks the assessment too (step 4)

The independent check (`check`) re-derives the result from the input and the assessment. The
expert's counter-criteria become platform checks: no result "open" without the missing fact, who
supplies it and by when; no conditional where the result is definite; no tenant letter without a
deadline; every legal assessment with its source.

### D5 — New tasks measure it (step 5)

A fix written knowing a solution turns that test into a regression test. Only tasks the platform was
not fixed for measure it: NK-05 and later, run before their solutions are seen, scored by D1.

## Not building

- A chatbot that answers in prose. The result stays structured and computed; the model assesses.
- Retrieval while the law texts fit (ADR-0041 D3).
- A model that decides who pays. It assesses, with sources; code computes; a person approves.

## Consequences

- Each case costs one model call more, and its assessment is part of its record.
- The builder writes less law into code: the definition names the questions, the assessment answers
  them per case, and the check holds both to the sources.
- The scoring sheets show where the platform stands against any other agent, in the expert's terms.

## Implementation steps

1. **Scoring sheets and the scorer (built).** `app/evals/scoring.py` (plain checks, the judge with
   quoted evidence, points, knock-outs, the usable threshold), `scripts/score.py` (scores the
   platform from the task's documents, a stored platform result, or another agent's answer),
   `JUDGE_MODEL`. Sheets for NK-01 to NK-04 with the expert's documents.
2. **The `assess` step (built).** A step kind (`app/kernel/procedures.py`): at most one, before
   every function step, naming the tools it may call; a definition that assesses has an
   `assessment` form. The run is held (`assessing`, `app/workflow_agents/runs.py`) and assessed
   outside the request (`app/assess/service.py`): claimed under the case's lock with a lease of 15
   minutes, so a crash mid-assessment is taken over by the next sweep once the lease runs out; the
   sweep runs beside the app every 20 seconds, and so after every restart. The model gets the case's
   input, the law texts, the workspace's documents and the case's files; every `{source, quote}` in
   its answer must name one of them and quote it word for word, else it is shown the misses once and
   then the run fails and says which. The functions after it receive `input["assessment"]`. A case
   whose input carries an assessment runs straight through: the publish gate runs code, never a
   model, so an assessing definition's test cases carry the assessment they compute with.
3. **Calculators as tools (built).** A definition declares `tools` (name, description, parameters as
   a JSON Schema, the function in `functions/` that runs it); the model calls them through OpenAI's
   tool calling (`app/assess/model.py`), each call runs in the sandbox, a call to a tool the step
   does not name is told to the model and not run, at most 24 calls, and every call with its
   arguments and result is kept on the run (`outputs.assess.tools`). `ASSESS_MODEL`, `gpt-5.5`.
4. **The gate's checks on the assessment.** Built: the quote check. Not built: the expert's
   counter-criteria as platform checks.
5. **Betriebskosten on the assess step,** its legal questions answered per case, scored from the
   task PDFs against the other agent; then **NK-05 and later**, scored before and after.
   - **5a, built (version 6): what the lease charges.** The model reads the lease from the case's
     documents and answers, per cost type of the case, whether the lease charges it to the tenant,
     the clause quoted (`forms/assessment.json`). The calculator and the independent check apply
     that reading; a cost type it did not answer is a question. The statutory side stays in the
     catalogue, which gains gutter cleaning (other operating costs, only where named) and
     modernisation (no operating cost). A field the code derives is marked `form: False` and is no
     part of the input form. The agreed advances are taken as the basis of a definitive settlement,
     no longer as an assumption to confirm. From the NK-03 task PDF, with the real model, part A
     now meets the expert's traps 1 to 5 and 12; part B (the rent increase) is 5b.
   - **5b, built (version 7): the rent increase under § 558 BGB.** The facts are read from the
     documents (area, the Mietspiegel's figure, when the request reaches the tenant, the rent's
     history with any § 559 surcharge, the working day); the one judgment, which cap applies (20 %,
     or 15 % where a state regulation names the area), is the assessment's, quoted. The code
     (`rent.py`) does the rest: the rent three years before it takes effect, the cap without
     surcharges, the local rent, the lower binding, the surcharge kept on top, the waiting periods,
     consent and start dates by § 558b, and a request of its own, with the reason for two letters;
     the check re-derives every figure by its own route. A § 559 step keeps the base rent before
     it, however its rent was read (the reader read the same line both ways). The days to the
     deadline are part of the result. The assessment's quotes may mark words left out with "…";
     it is corrected twice before it is refused. Under a reference to all of § 2 BetrKV the
     catalogue decides what is chargeable; the assessment's reading counts for a closed list and
     the other operating costs (the model had read smoke-detector maintenance against the
     expert's decision). End to end from the task PDFs with the real model, judged three times:
     NK-01 12/12, NK-03 17/17 in two runs, NK-02 13.5/16 (Frau Lindqvist's interim letter, see the
     open question). NK-03 was built knowing the expert's solution: it is a regression test now.
   - **5c, built (version 8): a settlement after its deadline, over several flats (NK-04).** The
     reader is told which unit the case is about (the case's title) and reads only that one; one
     landlord's documents for two flats are two cases. The assessment says per position whether the
     landlord is responsible for its being late, from when he could first have billed it, and what
     the obstacle was, quoting under `law` a text from the law sources (the check refuses a legal
     judgment that quotes only the facts) and under `facts` the documents. The module ships the
     official headnotes of BGH VIII ZR 249/15, 220/05, 264/12 and 379/20, and 249/15 in full
     (ADR-0041). The code (`late.py`) does the rest: the deadline, what is lost (the advances are
     set against the excluded positions first), what stays claimable and by when (three months
     from the obstacle falling away, 220/05), a credit always refunded, the recommendations, and a
     damages claim against a third party only as a question for a person; the check re-derives it.
     Whether an obstacle excuses is law, so it is code: a late statement of the owners'
     association never does (249/15 a, b), whatever the model judged. The model had judged so once
     in two runs and knocked the answer out; asking it the fact (which obstacle) instead of the
     legal conclusion is what made it stable, not voting over its opinion. End to end, judged three
     times: NK-04 17/18 in three of four runs (the fourth was the one before the rule), NK-01 12/12,
     NK-03 17/17, NK-02 12.5 to 13.5/16 (the interim letter; one point lost to the judge quoting
     the answer not word for word, refused by its own check). NK-03 and NK-04 are regression tests
     now; new tasks measure.
   - **5d, built (version 9): adversarial variants.** Tasks built against a solution only show
     that it was learned. A variant is a task's own text with one fact changed (a document
     removed, a clause added, a date moved, a heading's year wrong), run end to end, and held to
     plain checks on the outcome (a figure, a figure that must appear nowhere, a question that
     must be asked), not to a judge: what a variant tests is known in advance, so it needs no
     judgment (`scripts/adversarial.py`). An edit that no longer matches its task exactly once
     refuses the variant, so a drifted task never passes silently. The variants quote the
     expert's tasks and stay with the scoring sheets, private. The first eight found two gaps,
     now closed: a missing Mietspiegel failed as "an invalid value" instead of asking for a means
     of justification under § 558a BGB, and a statement headed with another year than the one
     settled was settled without a word (the case now carries the period the statement itself
     names, and a mismatch is a question). A timeout of the assessing model now says so. On version 9
     all eight pass (one only on a second run: the API had dropped the model calls, and the case
     failed closed), and the tasks hold: NK-01 12/12, NK-02 14.5/16, NK-03 17/17, NK-04 17/18.

## Open questions

- The judge's independence: `gpt-5.5` with evidence now, another provider when configured.
- The expert gave no usable threshold for NK-01 and NK-02; they are reported as points.
- NK-02: whether a tenant's interim letter while one position is open (with its method and date) is
  dispatchable, or must state a balance; the judge reads the counter-eval as the latter.
