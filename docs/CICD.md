# CI/CD

Two GitHub Actions workflows: **CI** (the quality gate, always on) and **Deploy** (continuous
delivery to Railway, dormant until credentials are added).

## CI — `.github/workflows/ci.yml`

Runs on every push and pull request. Two independent jobs so a failure in one is legible and
doesn't mask the other; both must be green to merge. A newer push to the same ref cancels the
in-flight run (concurrency group), so CI never stacks stale runs.

### `backend` job — lint · migrate · test
1. **Postgres 16 service container.** The DB-gated tests (persistence, studio flow, tenancy)
   would otherwise *skip*; a throwaway Postgres makes them run for real. The app reads
   `DATABASE_URL` first (`config.sqlalchemy_url`), so pointing it at the service needs no code
   change — CI sets `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres`.
2. **Install the engine from git.** `laufwise` is a separate public repo, not on PyPI, so CI
   does `pip install git+https://github.com/dfadeeff/laufwise.git` and then `pip install -e .[dev]`
   for the platform. (Locally it's an editable sibling install; same package, different source.)
3. **`ruff check`** — lint.
4. **`alembic upgrade head`** — build the schema in the CI database from migrations (this is
   also a real test that the migrations apply cleanly from scratch).
5. **`pytest`** — the full suite, DB tests included.

### `frontend` job — typecheck · build
`npm ci` → `npm run typecheck` (`tsc --noEmit`) → `npm run build`. The build gets **dummy Clerk
env vars**: `ClerkProvider` needs a *parseable* publishable key to prerender, but makes no
network call at build. The publishable key isn't a secret (it ships to the browser); the
`CLERK_SECRET_KEY` value here is a throwaway placeholder, never a real key.

## Deploy — `.github/workflows/deploy.yml`

Continuous delivery to Railway (PLATFORM_PLAN §6.4). It triggers **only after CI succeeds on
`main`** (`workflow_run` gated on `conclusion == 'success'`), plus manual `workflow_dispatch`.
A failed CI never reaches deploy.

**It is dormant until configured** — the guard step checks for `RAILWAY_TOKEN`; if absent it
emits a notice and the deploy steps are skipped, so the workflow stays green rather than red.
To enable:

1. **Secret:** Settings → Secrets and variables → Actions → `RAILWAY_TOKEN`.
2. **Variables:** `RAILWAY_BACKEND_SERVICE`, `RAILWAY_FRONTEND_SERVICE` (the Railway service
   names to deploy each subtree to).

Once set, each push to `main` that passes CI deploys the `backend/` and `frontend/` subtrees to
their Railway services. Railway's own Postgres and managed inference stay outside this workflow
(PLATFORM_PLAN §6.4); this pipeline ships the two stateless tiers.

## Schema and templates on deploy — `backend/railway.json`

`deploy.preDeployCommand` runs `alembic upgrade head && python scripts/seed.py` in the built image
before traffic switches to the new container. So a migration and a new runbook version ship with
the code that needs them, with **no manual step and no CLI** — which matters because forgetting
either is invisible: the app starts fine and then fails on the first request that touches the new
column, or keeps running the old template version while the catalog shows the new one.

It is a *pre-deploy* step rather than the container's `CMD` on purpose: a stalled database fails
the deploy and leaves the old container serving, instead of hanging uvicorn on every boot. Both
commands are idempotent — alembic skips applied revisions, the seed skips any `(name, version)`
already present — so they are a no-op on a deploy that changes neither.

**Instances still pin their template version** (ADR-0002 #15), so publishing v(n+1) does not move a
running instance: redeploy it in the studio, which offers that as a one-click update.

## What CI does **not** cover yet

- `mypy` (dev dependency is present; not run in CI until the type surface is stabilized).
- Frontend lint (no ESLint config in the repo yet).
- End-to-end / browser tests (no surface for them yet).
- A real production database in deploy (Railway provisions its own; migrations there run as the
  `preDeployCommand` above, not from CI).