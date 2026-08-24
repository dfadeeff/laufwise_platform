# Voice quality harness

`scenarios.json` is the versioned conversational test suite. It tests outcomes rather than exact
agent wording and includes happy paths, interruptions, hesitation, noise, quiet speech, regional
accents, domain vocabulary, numbers, multilingual turns, tool latency, unavailable slots, and
provider failures.

Validate the full suite:

```bash
cd backend
.venv/bin/python -m app.workloads.conversational.evals.harness
```

Inspect one coverage group:

```bash
.venv/bin/python -m app.workloads.conversational.evals.harness --tag interruption
```

Each scenario may add an `audio_file` relative to this directory. Once real WAV fixtures are
recorded, the loader fails closed if a referenced recording is missing. `turns` remains the expected
transcript so STT accuracy and conversation behavior can be scored independently.

The `environment` object describes the adapter behavior a replay runner must inject. It must never
call a production booking provider. In particular, `delay_ms`, `error`, `result`, and
`postcondition` exist so the same transcript can prove announcements, fail-closed behavior, and
the rule that a write acknowledgement is not booking confirmation.
