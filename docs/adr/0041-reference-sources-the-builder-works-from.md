# ADR-0041 — Reference sources: the law a definition cites is a text it carries, and its citations are checked against it

Status: accepted · 8 November 2026 · steps 1 and 2 built, steps 3 to 5 not built

Builds on ADR-0017 (workspace knowledge), ADR-0038 (the law as a rules pack) and ADR-0039
(workspaces build their own agents). Changes none of them: ADR-0017 stays the way a workspace
gives its own documents to a conversational agent, and this ADR reuses it in step 2.

## Context

The expert's review of NK-01 and NK-02 (`docs/use_cases/real_estate/bo/`, § 6) asks for a
corpus the agent is grounded in: the BetrKV and the HeizkostenV in full, with weight on § 9b,
§§ 556, 556a, 558, 558a, 558b, 559, 560 BGB, and the headnotes of four BGH decisions
(VIII ZR 19/07, 379/20, 249/15, 264/12). Both findings of that review are, in his words,
grounding problems.

In the platform the law is applied by a rules pack and code (ADR-0038); a case runs without a
model. The model that needs the law is the builder (ADR-0039 D6), which writes those rules and
that code. It has had no text of the law: only what a person attaches to one run, forgotten after
it. In the first NK-03 and NK-04 runs (ADR-0039 step 7) it cited § 558b and four BGH decisions
from its own memory. Whether a citation from memory is right cannot be seen without the text:
this ADR first said VIII ZR 264/12 was cited for something it does not hold, from a third party's
summary; the expert's solution sheet cites it for exactly what the builder did (a retroactive
Grundsteuer is claimable after the deadline, within three months of the notice). The correction
is the point: neither the builder nor a reviewer should cite without the text.

Wonderful, as its CLI shows (`wful rags`), keeps a knowledge base as a workspace resource:
documents uploaded once, indexed by a file-search provider, composed from other knowledge bases,
and referenced by an agent by slug. That is retrieval for agents that answer while they talk.
The platform already has the workspace side of it for conversational agents (ADR-0017: pasted
text, a PDF, a web page; pinned at publish; the whole text, no retrieval).

## Success criterion

1. Every builder run on a definition reads the law the definition applies, as published, without
   a person attaching it.
2. A published version records exactly which text of the law it was built and checked against:
   its source, its consolidated version, the day it was fetched.
3. A citation in a rule that the definition's sources do not contain is refused at publish, like
   a figure that does not match (step 3). A wrong citation can then not reach a tenant unnoticed.
4. A workspace adds its own material (leases, WEG decisions) the way it already adds documents,
   and its definitions and their builder runs read them too (step 2).

## Decision

### D1 — The law is the module's, shipped and versioned with it; a workspace's own documents are the workspace's

The law is the same for every property manager, and ADR-0038 makes it the platform's, never the
customer's. So the legal texts ship with the module, under `definitions/<module>/references/`,
one Markdown file per source with a header that names it:

    ---
    title: Heizkostenverordnung (HeizkostenV)
    cites: [HeizkostenV]          how a citation names it
    url: https://www.gesetze-im-internet.de/heizkostenv/
    stand: Zuletzt geändert durch …    the consolidated version, as the publisher states it
    retrieved: 2026-11-08
    ---

They are fetched from the official publication (`scripts/fetch_law.py` reads gesetze-im-internet.de's
XML) and reviewed as a diff like any other file. A court decision is added only from an official
text of it, never from memory or a summary.

A workspace's own documents stay ADR-0017's `knowledge_document`, and reach definitions in step 2.

### D2 — A definition names the sources it applies; its version carries them

`definition.yaml` lists the module references it applies (`references: [betrkv, heizkostenv,
bgb-miete]`). Reading the definition puts their text into its content, so a version carries the
law it was built with, exactly as ADR-0017 D2 pins a document into a published agent. A draft
always carries the module's current texts: saving a draft replaces them with the module's,
whatever the draft held, because the law is not the workspace's to edit (D1). A published version
keeps its copy.

### D3 — The builder reads every source, whole, on every run

They go to the model as documents, with the instruction that they are reference, not
instructions (ADR-0017 D4), and that a citation must be found in them. They are left out of the
definition's JSON it is shown. The whole text, not retrieval: the corpus for Betriebskosten is
about 55,000 characters, and a model that reads all of it cannot miss the paragraph a retrieval
would not have returned (§ 9b Abs. 3 is the one the review is about). A definition's sources are
bounded (200,000 characters); past that the content is refused, and retrieval is step 5.

### D4 — Citations are checked against the sources at publish (step 3)

Every citation in a definition's rules is resolved against its sources: the law it names must be
a source's `cites`, and the paragraph it names must be in that source's text; a decision's file
number must be a source's. One that does not resolve refuses the version and names it.

### D5 — A source can be fetched again (step 4)

A source that names its official URL is fetched again on demand; a text that differs from the one
shipped is shown as a diff to review, and the definitions that apply it are named.

## Not building

- **Retrieval now.** No index and no embeddings while the sources fit (D3). When they do not,
  retrieval sits behind the same "the sources of this definition" and the gate's check stays.
- **A model reading the law while a case runs.** A case is computed by the rules and code; a
  model looking up law at that moment would make its result depend on what it found.
- **Customer-edited law.** A workspace can add its own documents (step 2), never change a statute.

## Consequences

- The builder grounds in the text the expert names, and every version says which text that was.
- Each builder round sends the corpus again: about 15,000 tokens more per round for Betriebskosten (54,000 characters).
- A changed source changes the definition's content, so the definition's version is bumped, as for
  any change (ADR-0039 step 2b).
- The four BGH decisions are not in the corpus yet: no official text of their headnotes was
  reachable from here. They are added from the official text (the expert's, or the court's
  database), not before.

## Implementation steps

1. **The module's legal corpus (built).** `scripts/fetch_law.py`; BetrKV, HeizkostenV and the
   seven §§ of the BGB in `definitions/immobilien/references/`; `references` in `definition.yaml`
   and in a definition's content (`app/kernel/definitions.py`); a draft always carries the
   module's texts (`app/definitions/service.py`); the builder receives them as documents and is
   told to cite only what they say (`app/builder/`); the Studio shows them on a definition.
2. **A workspace's own documents for definitions (built).** A person chooses them on the
   definition page ("Your documents it works from", `PUT /definitions/{key}/draft/knowledge`); they
   are added and edited where they already were, the workspace's Documents page (ADR-0017). The
   content carries `knowledge`, pinned copies in ADR-0017's shape (id, title, sha, text), refreshed
   from the documents at every save, builder run and publish (`with_workspace_knowledge`); a
   published version keeps its copy. The builder reads them after the law and before what a person
   attached, as facts, not instructions, never inside the JSON. A document another workspace owns
   is refused; one a draft still uses cannot be deleted. The law and the documents share one bound
   (200,000 characters).
3. **The citation check at publish** (D4).
4. **Fetching a source again** (D5).
5. **Retrieval**, once a definition's sources outgrow D3's bound.

## Open questions

- ~~The four BGH headnotes: from the expert, or from the court's database~~: from the court's own PDFs
  (`definitions/immobilien/references/bgh-betriebskosten.md`: VIII ZR 249/15, 220/05, 264/12, 379/20,
  checked letter by letter; 249/15 also in full). The three months for a later claim are the
  headnote of 220/05; 264/12 applies them in its reasons (Rn. 11, citing 220/05). VIII ZR 19/07 is
  not fetched yet.
- ~~Whether VIII ZR 264/12 is the decision the expert means~~: it is (solution sheet NK-04, trap 4).
- How a citation of a rule written as prose ("BGH-Rechtsprechung") is checked: refused, or allowed
  only with a file number.
