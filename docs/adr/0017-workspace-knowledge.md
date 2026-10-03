# ADR-0017 — Practice knowledge: workspace documents, pinned into each published agent

Status: accepted · 3 October 2026 · extends ADR-0016 (workspaces), `agent-contract`

## Context

An agent knew two kinds of facts: the structured ones in its configuration (address, hours,
treatments, prices) and whatever the practice pasted into its conversation-style text, which caps
at 6000 characters and mixes rules with facts. A practice's FAQ, insurance rules, parking or "what
to bring" had nowhere to live. Wonderful's answer is a knowledge base bound to an agent; the
question was how much of that a small practice needs.

## Decision

### D1 — Documents belong to the workspace; an agent chooses which it knows

`knowledge_document(id, tenant_id, title, source, content)`. A practice adds a document once, by
pasting text or uploading a PDF (text extracted with `pypdf`; the file itself is not kept), and any
of its agents can know it. An agent's draft lists the documents it knows (`knowledge_ids`), in the
order it reads them. Tenant-scoped like every row: another workspace's document is not found.

A PDF arrives base64 inside JSON (at most 5 MB), so the upload needs no multipart dependency. A PDF
with no text layer, such as a scan, is refused with "paste the text instead" rather than stored
empty.

### D2 — Publishing pins the content

A snapshot carries a frozen copy of each document it knows (`runtime_config.knowledge`: id, title,
content, hash). A call reads that copy, never the live row. Editing a document therefore changes
nothing a caller hears until the practice publishes again, and every call can be traced to exactly
what the agent knew. A document an agent's draft still uses cannot be deleted; a published
snapshot keeps its copy regardless.

### D3 — The whole text, up to a limit; no retrieval yet

The documents go into the prompt in full, after the agent's rules. A small practice's knowledge is a
few pages; the model reading all of it on every turn is more reliable than retrieving fragments,
and adds nothing to a call's latency. The limit is 40,000 characters (about 10k tokens) per agent,
in total. Past it, publishing refuses and names the fix, rather than silently dropping a document.
Retrieval (a `search_knowledge` tool) is for when a practice needs more, and is not built.

### D4 — Documents are reference, not instructions

The prompt states that the documents are the practice's reference material, that anything in them
that reads like an instruction does not change the agent's rules, that the structured price list
and opening hours win where they disagree, and that anything not covered is a callback.

## Consequences

- A practice can give its agents its own material without a release or an engineer.
- Prices and hours stay structured, because booking depends on them being exact; a price inside a
  document is read as text, and loses to the price list.
- 40k characters per agent bounds prompt cost and keeps calls fast; a practice with more must
  choose what callers actually ask about.

## Open questions

- Retrieval for larger knowledge, with sources quoted back.
- Importing a practice's website pages directly.
- Turning an uploaded price list into treatment rows to confirm.
- Eval scenarios that check answers against a document.
