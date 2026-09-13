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

Audio fixtures are the missing half. Once a scenario has an `audio_file`, the loader fails closed
if the recording is absent, and the 11 skips become real runs.
