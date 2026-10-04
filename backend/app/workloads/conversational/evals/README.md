# Voice quality harness

`scenarios.json` is the versioned conversational test suite. It tests outcomes rather than exact
agent wording and covers happy paths, interruptions, hesitation, noise, quiet speech, regional
accents, domain vocabulary, numbers, multilingual turns, tool latency, unavailable slots, and
provider failures.

The harness has two modes. Validating the file is free and runs in CI; replaying scenarios against
the real agent costs model calls and is always opt-in.

## Validate (free)

```bash
cd backend
.venv/bin/python -m app.workloads.conversational.evals.harness
.venv/bin/python -m app.workloads.conversational.evals.harness --tag interruption
```

## Replay against the agent (costs model calls)

```bash
.venv/bin/python -m app.workloads.conversational.evals.harness --run                 # whole suite
.venv/bin/python -m app.workloads.conversational.evals.harness --run --tag happy_path
.venv/bin/python -m app.workloads.conversational.evals.harness --run --id de-correction
```

Needs `OPENAI_API_KEY`. Exits non-zero if anything failed. `--limit` keeps an exploratory run cheap.

### Which agent is replayed

Without `--config`, the knowledge-base agent: `base.md` over `knowledge/muenchen.yaml`, the one
the suite was written against. **No published agent runs that prompt.** Every agent configured in
the Studio runs `studio.md` plus its customer instructions, its own practice and its own
capabilities (change and cancel are withheld). `--config` replays one of those:

```bash
.venv/bin/python -m app.workloads.conversational.evals.harness --run --tag smoke \
  --config app/workloads/conversational/evals/studio_agent.json
```

`studio_agent.json` is the same practice entered through the Studio. A test holds it to the publish
gate, so it is a configuration a practice could actually go live with. The report's snapshot names
the prompt file and a hash of the configuration, so a result says which agent passed.

### Nightly in CI

`.github/workflows/evals.yml` replays the `smoke` set (9 text-replayable scenarios: booking in
three languages, the fail-closed paths, safety, escalation and knowledge) against both agents
every night at 03:17 UTC. It can also be started by hand from the Actions tab, with any tag and
repeat count. It never runs on a push or a PR: a model's coin-flip must not decide whether a
deploy can merge, so `ci.yml` stays the merge gate and this is the trend line. Each run's report
is kept as a workflow artifact. Needs the repository secret `OPENAI_API_KEY`; without it the job
says so and stops cleanly.

The realtime (speech-to-speech) engine is not replayed. This suite drives the model through
chat completions with text turns, and a realtime model is reached over its own audio session, so
a pass here says nothing about how a realtime agent sounds or takes turns (the snapshot says
`transport: cascaded` for exactly that reason).

Every run is kept — `runs/evals/<timestamp>-<prompt_sha>.json`, with `latest.json` pointing at the
newest (`--reports` to change the directory). Each holds the full record: transcript, every tool
call with its arguments and result, appointments created, and judge verdicts.

Reports are kept rather than overwritten because the useful question is not "did it pass?" but
"did this change help?", and a total hides a change that fixes four scenarios and breaks three:

```bash
.venv/bin/python -m app.workloads.conversational.evals.harness \
  --compare runs/evals/<before>.json runs/evals/latest.json
# fixed:  de-correction, regional-bavarian, self-correction-same-turn, ...
# broken: de-doctor-name, english-request
```

A replay uses the **production** instructions (`prompts/base.md`), the **production** tool
definitions (`booking.TOOLS`) and a real `BookingSession`, so a booking in an eval passes through
the same governed contract as a booking on a live call. Only the audio layer is absent: `turns` are
the transcript STT would have produced.

## How a scenario is judged

Three layers, in order, and the cheap ones settle it first:

1. **Skip.** A scenario whose `environment` can only exist in audio — `interrupt_at_ms`, `overlap`,
   `backchannel`, `internal_pause_ms`, `noise`, `snr_db`, `gain_db`, `stt_confidence` — is reported
   as skipped with its reason. It is never counted as a pass. A suite that reports 46 passes when
   11 of them never ran is worse than no suite.
2. **Invariants.** Decidable failures never reach a judge: an appointment that exists without a
   booking that returned `ok`, or more than one appointment for one caller.
3. **Judge.** The prose expectations are ruled on by a model that is given the tool record as well
   as the transcript, and told the record is the truth. An agent that says "you're booked" while
   `appointment_book` returned `blocked` fails however fluent it sounded.

Each run is stamped with a snapshot — prompt hash, contract version, tool names, model — because a
pass means nothing without the version it passed against. Passing an eval never publishes anything.

## Fault injection

`environment` makes the named tool misbehave, below the tool, so the tool and the governed step
stay real:

| `environment` | what happens |
|---|---|
| `result: "empty"` | availability comes back with no slots |
| `error: "timeout"` | the calendar is unreachable — must never read as "nothing is free" |
| `result: "write_acknowledged"`, `postcondition: false` | the write is acknowledged and nothing persists; the postcondition must catch it |
| `result: "slot_taken"` | someone takes the slot between choosing and booking; the real precondition refuses it |

## Where the suite stands

Last full replay: **18 of 35 runnable scenarios pass, 11 skipped for audio** — up from 11 before a
prompt fix the suite itself exposed (the agent was dropping details the caller gave in their
opening sentence). `--compare` showed that change fixed 9 scenarios and broke 2. The remaining
failures are worth reading before treating any of them as a bug:

- Some expect capability that is not built — a phone number, a treatment type, a named
  practitioner. This agent collects three details and books.
- Some are fragments of a longer call (`"Ja, buchen."`) and presuppose a state a single-turn replay
  cannot reach.
- `language-choice` expects the agent to switch language mid-call; `base.md` deliberately forbids
  that. One of the two has to change, and it is a product decision, not a bug.

First replay of the smoke set against a Studio agent (2026-10-03, one attempt each, so noisy):
**knowledge-base 7/9, Studio 5/9.** The Studio agent's extra failures are real gaps rather than
noise worth re-rolling: it said a slot was taken without checking availability, it has no wording
for "closed at the weekend", and its callback request was refused for want of a confirmed phone
number. Both agents failed to escalate after a write that was acknowledged and never persisted.

Audio fixtures are the missing half. Once a scenario has an `audio_file`, the loader fails closed
if the recording is absent, and the 11 skips become real runs.
