# ADR-0023 — A call survives a deploy, and a backend outage reaches a person

Status: accepted · 18 October 2026

## Context

Before a practice forwards its real line to an agent, two failures had to stop:

- **Every deploy ended every call.** One Railway container carries all calls as websockets.
  Railway stops the old container with SIGTERM once the new one is up, and uvicorn's shutdown
  closes every websocket with 1012. Reproduced in `tests/test_draining.py` against plain
  `uvicorn.Server`: `received 1012 (service restart)`.
- **A backend that does not answer left the caller with Twilio's error tone.** Numbers had no
  fallback URL.

## Decision

### D1 — The server waits for its live calls before it stops

`app.serve.DrainingServer` (uvicorn with the same settings) counts calls through
`live_calls.track()`, around each call's pipeline and its summary email. The first SIGTERM waits
until the count is zero, at most `MAX_CALL_SECONDS` + 60 s, then stops as uvicorn would; a second
signal stops at once. New calls already go to the new container. Railway is configured to allow the
wait (`RAILWAY_DEPLOYMENT_DRAINING_SECONDS=660`) and to switch traffic only once `/api/v1/health`
answers.

### D2 — Twilio's fallback lives on another host

Each number's `VoiceFallbackUrl` points at `/api/telephony/fallback` on the Studio frontend
(Vercel), not on this backend, which would fail exactly when needed. With the agent's transfer
number (written in at activation) the caller is put through to staff; without one they hear a
German sentence and the call ends. The route only returns TwiML and validates the number as E.164,
so it needs no secret.

## Consequences

- Merging during the day no longer cuts calls; a deploy can take up to 11 minutes to retire the old
  container while a call runs.
- A crash, an out-of-memory restart or a Railway incident still ends the calls in progress; the
  fallback covers the next callers, not the current ones.
- The transfer number must not be the practice line that forwards to the agent, or the fallback
  rings the agent again.
