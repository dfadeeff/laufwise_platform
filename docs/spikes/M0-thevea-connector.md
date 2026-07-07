# M0 spike — thevea connector feasibility (2026-07-07)

Gate for ADR-0003. Question: can a governed agent authenticate as a real thevea user and
read/write their calendar headlessly? Findings below are from unauthenticated recon only
(no credentials used yet). The one authenticated step is left for the account holder.

## What thevea actually is (corrected)

- `thevea.de` is the **WordPress marketing site** (`wp-login.php`) — NOT the app. Ignore it.
- The real practice-management app is **`https://mein.thevea.de`** — an **Angular** SPA
  (`main-*.js` + `polyfills-*.js`, `chunk-*.js`).
- Wildcard DNS: every `*.thevea.de` resolves to `49.13.175.35` (Hetzner, EU — good for GDPR).

## The API (the important part)

- **Single GraphQL endpoint: `https://mein.thevea.de/graphql`** — live, `POST`, returns
  `{"data":{"__typename":"Query"}}` unauthenticated.
- Backend is **HotChocolate (.NET GraphQL)** — identified by error code `HC0046`.
- **Introspection is disabled** (`HC0046`), so we can't dump the schema. But every operation
  the app uses is compiled into the Angular chunks, and HotChocolate's errors are precise
  ("The field `X` does not exist on the type `Mutation`"), so the schema is recoverable field
  by field from real traffic.
- Client transport: **Apollo Angular**, same-origin, `uri:"graphql"`, `POST`. There is also a
  **GraphQL subscriptions / WebSocket** layer (live calendar updates).

## Auth mechanism

- **No 2FA, no CAPTCHA, no SSO** on the login surface — plain username/email + password.
  This is the single biggest de-risk: headless login is viable.
- Session looks **cookie-based**, not bearer-token: the app uses a `LoginStatus` route guard
  with `reloadLoginStatus()`, and there is no `Authorization: Bearer` header construction in
  the bundle (the only `Jwt`/`TokenGQL` strings belong to the Zendesk support widget). So the
  connector keeps a **cookie jar / session**, not a token header. (Confirm on the first real
  login — see below.)
- The GraphQL login field is **not** `login` or `loginUser` (both confirmed absent). Its real
  name is recoverable in one DevTools capture.

## Domain model (from chunk symbol names — GraphQL types)

Rich appointment model, all German (`Termin` = appointment):
`Terminfinder` / `Terminvorschlaege` (**appointment-slot finder / suggestions** → our
availability read), `TermineForToday`, `TermineForPatient`, `TerminEvent`, `TerminErinnerung`,
`Terminkategorien`, plus create/modify/delete appointment mutations. This maps directly onto
ADR-0003's bindings:

| Runbook binding | thevea operation (to confirm) |
|---|---|
| `calendar.has_free_slot` (precondition) | `Terminfinder` / `Terminvorschlaege` query |
| `book_appointment` (tool) | appointment-create mutation |
| postcondition re-query | `TermineForToday` / by-window query (read path ≠ write path ✓) |

Non-circularity holds: availability read and appointment write are distinct GraphQL operations
on a system we don't control.

## Verdict: **GREEN, with one user-supplied step**

Nothing structural blocks the connector. The mechanism is a cookie-session GraphQL client — the
simplest possible shape. Remaining unknowns are all recoverable by the account holder in minutes:

1. **Exact login mutation** (name + variables + response) — DevTools capture.
2. **Session confirmation** — does login set an httpOnly cookie the client reuses? (Expected.)
3. **Session lifetime** — how long before re-auth; is there a refresh op?
4. **`Terminfinder` + create-appointment signatures** — two more DevTools captures.
5. **ToS** — read `mein.thevea.de` / account terms before anything external-facing; we act
   only within the logged-in user's own permissions on their own data.

## How to finish M0 (account holder, ~10 min)

1. Log into `mein.thevea.de` with DevTools → Network → filter `graphql`.
2. Log in; copy the **login** request's `operationName`, `query`, `variables`, and check the
   **Response Headers for `Set-Cookie`**.
3. Open the calendar; copy the **availability** query and one **create-appointment** mutation.
4. Paste the login + availability operations into `backend/spikes/thevea_login_spike.py` and
   run it with `THEVEA_USER` / `THEVEA_PASS` set. Green = login cookie obtained + one
   authenticated read succeeded → M0 done, proceed to M1.

## Notes for M1 (the connector)

- `TheveaClient` = an `httpx.Client(cookies=...)` posting GraphQL to `/graphql`; re-auth on the
  "not logged in" error class; raise `StateUnavailable` on any unexpected shape or transport
  error (never act on garbage — ADR-0003 D1).
- Store the credential (D3: Fernet-encrypted in `Connection.tokens_enc`) — heavier custody than
  a token; migrate to a token/OAuth handshake if thevea ever partners.
- The subscriptions/WebSocket layer is not needed for booking; ignore it in v1.