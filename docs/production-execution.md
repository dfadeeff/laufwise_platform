# Production execution guarantees

The workflow tier has two execution paths. Case procedures compute in a sandbox and persist the
case/run together. Connector runbooks perform external actions through the control-plane facade.
Live voice actions use that same facade, injected by their conversation recorder on the existing
tool thread. Transcript recording remains best effort; a tool action without durable intent fails
closed without blocking the audio event loop.
An external service and Postgres cannot share one transaction, so a lost acknowledgement is an
unknown outcome, never evidence that it is safe to repeat a write.

## Connector runbooks

The facade commits the run, pinned template version, owner and canonical input hash before execution. Raw
patient/calendar input and tool arguments are excluded from intent records. Each tool
call commits an `action_intent` with its action key before invoking the implementation. A return
is an `action_returned` claim; the engine's postcondition trace is committed independently before
it can advance to another step. A failed journal write stops execution.

The run begins with `lifecycle=unverified` and conservative `status=state_unavailable`. Only the
final commit sets `lifecycle=completed` and the actual governance outcome. A crash or failed final
commit leaves intent and previously committed evidence readable through the tenant-scoped run API.
The durable trace reference is `db:run:<id>`; there is no dependency on container disk. The run API
exposes intent/claim events in `journal`, including when there are no completed steps yet.

This is not exactly-once execution or automatic recovery. Reconcile an incomplete action with its
destination before retrying it. Existing connector idempotency checks still re-read destination
state; a new run id by itself does not deduplicate an external operation.

## Builder ownership

Builder requests serialize on the definition row. A queued run has a 90-second dispatch lease;
one execution claims it with a unique owner and renews that lease every 30 seconds. Startup and a
periodic sweep interrupt only expired leases, preserving another replica's live work.

The final save locks and checks ownership without autoflushing stale output. The draft and final
run status commit in one transaction. An expired owner cannot renew or save. A stopped process's
job becomes interrupted after lease expiry and must be started again; model work is not replayed.

Assessments use their existing case-run lease and recovery sweep. Completion is also fenced on
the exact claim token: a slow model response from before a takeover cannot complete the new
owner's suspended step, even when the case revision is unchanged.

For the first upgrade from a version without leases, let existing builders finish before rolling
out: an old binary does not know how to renew a lease or fence its own writes. Historical active
rows without a lease are treated as interrupted by the new binary.

## Approved letter delivery

The case lock and the approved fingerprint are checked again before each dispatch. Before SMTP,
the approval stores `sending`, a stable Message-ID and an audit event in a committed transaction.
The receipt is committed independently for each letter.

An explicit SMTP rejection becomes `failed` and may be retried. A disconnect or timeout may follow
server acceptance; it becomes `uncertain`. A process crash leaves `sending`. Neither state permits
automatic resend, and the Studio shows that provider reconciliation is required. This release does
not add a provider-specific reconciliation integration or claim SMTP proves delivery to an inbox.

## Deployment and network boundaries

Authentication is required by default. `APP_ENV=local` or `test` explicitly opts into unsigned
development access; an unrecognized value refuses settings construction. Set production explicitly
in hosted deployments and do not deploy development env files.

The deployment workflow checks out the exact triggering CI SHA and requires a successful push CI
run for that SHA on main, including manual dispatches. Pull-request/fork CI cannot authorize a
production deployment. Runtime configuration and host-level deployment settings must be checked
separately; a workflow definition alone cannot prove what a host currently runs.

URL imports use a public-only TCP backend: every DNS answer is checked and the socket connects to
a checked literal IP. The original hostname remains the Host header and TLS verification name.
Environment proxies are disabled and redirects receive the same checks.

## Verification

Regression tests cover authentication defaults, DNS rebinding/private addresses, write refusal
when the journal is unavailable, preservation of intent after a failed completion, builder lease
reclamation, stale-save refusal, and an uncertain SMTP receipt that cannot be resent. Disposable
Postgres tests additionally check cross-session audit survival, tenant scoping, exclusive claims,
and concurrent queue requests. CI applies the complete migration chain before running them.

These operational guarantees do not establish the legal correctness of a model's assessment.
An assessment/evaluation design must separately address applicable authorities, evidence supporting
each conclusion, independent checks, knockout criteria and unseen cases.
