---
name: agent-contract
description: The agent as a versioned, reviewable artifact — config schema, prompts as files, drafts/publish/promotion, append-only history, and what stays outside the agent (connections, credentials, triggers). Read before changing the Template/AgentInstance model, the publish gate, or anything about how an agent's definition is stored, versioned, or moved between environments.
---

# The agent contract — an agent is an artifact, not a row

The structural lesson that matters most is not about agents at all. It is this:

> **An agent's definition is a versioned, reviewable, immutable artifact.**

Not a database row with a prompt column you edit in place. A schema-validated config plus content
files, with drafts, diffs, publishes, and an **append-only history** — where every published
version and every past run can point at an exact revision.

That one decision is what makes agent changes reviewable, testable, revertable, and promotable
between environments — i.e. what makes the thing a *platform* rather than a prompt editor.

## The end-state layout

Once an agent has prompts and skill packages to hold, its definition wants to be a directory:

```
agent/
  config.json          # schema-validated: profile, behavior, model, prompt paths
  base.md              # the agent's instructions
  summary.md           # how to summarize an interaction
  tags.md              # how to tag an interaction
  skills/<name>/
      skill.json       # name, display_name, description, prompt path, tools
      prompt.md        # (a task-tier skill instead ships SKILL.md + its reference material)
  tools/<name>/
  evals/scenarios/  evals/batches/
  tags/  metrics/
  shared/              # reusable tool implementations, versioned separately + pinned
```

`config.json` points at the prompts **by path** — configuration and content stay separate, and both
stay versioned.

## The config schema is the guardrail

Validate the config against a generated JSON Schema, and let that schema encode the taxonomy
(see `agent-taxonomy`). Use **conditional rules**, not prose:

- a task-tier agent **requires** its containment/runtime block;
- a task-tier agent **forbids** a voice block;
- a stable identifier is `pattern: ^[a-z0-9-]+$`, distinct from the human display name.

**Illegal combinations become unrepresentable, not documented.** This is CLAUDE.md §0
"fail-closed / least surprise" expressed as a schema.

laufwise's equivalent today is `app/templates/contract.py` + the publish gate. It should grow the
same conditional shape as `agent_class` gains values: a `workflow` template carrying a
conversational surface field is a **schema error**, not a lint.

## Identifiers vs display names

| Entity | Identifier format | Rule |
|---|---|---|
| Agent | lowercase, digits, hyphens | derived from the display name at creation, then **independent** |
| Skills, tags, metrics | lowercase, digits, hyphens, underscores | same |
| Tools | one `name` — it is *both* the folder name and the call name | no display name at all |

Renaming a display name never moves anything. laufwise's `Template.name` is already the stable key;
keep any human label separate from it.

## The lifecycle

```
line of work ──► personal draft ──► snapshot ──► evals ──► diff review ──► publish or PR ──► head ──► traffic
```

| Concept | Meaning |
|---|---|
| **Draft** | *your private working copy*. Saving updates the draft, never the published version |
| **Snapshot** | a pinned state used for previews and evals, so a test refers to a known version |
| **Publish** | applies the draft to its target, then clears the draft |
| **Review** | required when the target is protected; optional elsewhere |

Rules that carry real weight:

- **A draft can never receive live traffic.** Ever. Not for a canary, not for a test.
- **A protected target cannot be published to directly** — you publish to a new line of work and
  open a review. Policy lives in the platform, not in a team norm.
- **A snapshot is what evals run against**, so "it passed" refers to an exact version. A passing
  eval does **not** publish anything.
- **History is append-only.** Published versions and past runs reference exact revisions; rewriting
  history could make one unreachable. Add on top instead.

laufwise already has the last one, in a different form: a published template version is immutable
and the seed skips an existing `(name, version)`. Same invariant, same reason. Say so in the ADRs.

## The reuse seam

Reusable *tool implementations* live in a shared library, versioned independently and **pinned to an
exact revision** by whoever depends on it — so two lines of work can test different shared code in
isolation. If an agent depends on shared code that has not been released yet, the agent's merge is
**blocked**. Ordering is enforced, not remembered.

Note the *anti*-rule that goes with it: **skills belong to one agent. Share tools and helper
modules, never skills** — because sharing a skill silently shares its guardrails, and two agents
that appear to need "the same" skill almost always need different ones.

## Promotion between environments

Promotion moves the *same* agent dev → staging → prod by **opening a review in the target**,
keeping histories connected so the next promotion is a reviewable diff rather than a rebuild. If a
newer shared-library revision is needed, **its review is handled first**.

The part everyone learns the hard way:

> **Promotion moves versioned behaviour. It does not create external resources.**

| External resource | Scope |
|---|---|
| Phone numbers, voices | tenant |
| Channel connections, knowledge bases, integration connections | workspace |
| Environment variables / global config (`{{global.X}}`) | per-environment — the *reference* is promoted, never the value |

An unchanged definition can still behave differently when one of its external resources is renamed,
removed, or configured differently. Hence **two validation levels**: local (structure and JSON only)
and **remote against the target environment** (resolves that environment's values and applies the
full schema). Run the remote one once per target.

**This is precisely laufwise's template/instance split, and laufwise got it right first.** A
template references *roles* (`source`, `destination`); an `AgentInstance` binds each role to a
`Connection` whose credentials never leave the tenant. Promoting a template is meaningless without
the target tenant's connections existing — check that at **deploy time**, not at first run.

## What stays OUT of the versioned artifact

| Thing | Why it's outside |
|---|---|
| **Connections / credentials** | per-tenant, encrypted, never in a template (`app/connections/crypto.py`) |
| **Triggers** | operational; ops must be able to disable an entrypoint without a release |
| **Phone numbers, channels** | tenant infrastructure |
| **Environment-specific config values** | environment-specific by definition |
| **Traffic routing** | an operational decision about which published head gets sessions |

The test: *would you want to change this at 3am without a code review?* If yes, it is operational
state, not artifact.

## What laufwise should adopt now, and what it should not

**Adopt:**
- Conditional schema validation in the publish gate as `agent_class` grows.
- An explicit `draft → published` story for **instances**, not just templates.
- Deploy-time verification that every referenced external resource resolves in the target tenant.
- Immutability + append-only history stated as an invariant in the ADRs, not merely implemented.
- Prompts as versioned files, not DB strings — **once there is a prompt at all**.

**Do not adopt yet:**
- A full repo-per-agent with pinned submodules. That is the right end state for a multi-tenant agent
  marketplace and enormous overkill today (§III). The property you actually need now is *"the
  definition is a reviewable, versioned, immutable artifact"* — which the `Template` row with an
  immutable `(name, version)` and a JSONB contract already provides. Move to files when there are
  prompts and skill packages to hold, not before.

## Rules

1. A published version is immutable. Bump, never mutate.
2. History is append-only. No rewriting a published contract.
3. Drafts never take traffic.
4. Identifier ≠ display name; renaming a label moves nothing.
5. The schema rejects cross-tier fields. Illegal shapes are unrepresentable.
6. Credentials, triggers, and channel bindings live outside the artifact, scoped by tenant.
7. Validate against the *target* environment, not just locally.
