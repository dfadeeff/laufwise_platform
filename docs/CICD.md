# CI/CD

Three GitHub Actions workflows: **CI** (the quality gate, always on), **Deploy** (a Railway CLI
deploy, dormant until credentials are added) and **Voice evals** (a nightly model replay, never a
merge gate). Production deploys themselves come from the hosts' own GitHub integrations — Railway
for the backend, Vercel for the frontend (`DEPLOY.md`) — not from `deploy.yml`; see below.

## CI — `.github/workflows/ci.yml`

Runs on every push and pull request. Two independent jobs so a failure in one is legible and
doesn't mask the other; both must be green to merge. A newer push to the same ref cancels the
in-flight run (concurrency group), so CI never stacks stale runs.

### `backend` job — lint · migrate · test
1. **Postgres 16 service container.** The DB-gated tests (persistence, studio flow, tenancy)
   would otherwise *skip*; a throwaway Postgres makes them run for real. The app reads
   `DATABASE_URL` first (`config.sqlalchemy_url`), so pointing it at the service needs no code
   change — CI sets `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres`.
2. **Python 3.12; install the engine from git.** `laufwise` is a separate public repo, not on PyPI, so CI
   does `pip install git+https://github.com/dfadeeff/laufwise.git` and then `pip install -e .[dev]`
   for the platform. (Locally it's an editable sibling install; same package, different source.)
3. **`ruff check`** — lint.
4. **`alembic upgrade head`** — build the schema in the CI database from migrations (this is
   also a real test that the migrations apply cleanly from scratch).
5. **`pytest -q`** — the full suite, DB tests included (`APP_ENV=test`).

### `frontend` job — typecheck · build
Node 20. `npm ci` → `npm run typecheck` (`tsc --noEmit`) → `npm run build`. The build gets **dummy Clerk
env vars**: `ClerkProvider` needs a *parseable* publishable key to prerender, but makes no
network call at build. The publishable key isn't a secret (it ships to the browser); the
`CLERK_SECRET_KEY` value here is a throwaway placeholder, never a real key. The job also sets
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` for the build.

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

Once set, each push to `main` that passes CI runs `railway up --path-as-root backend` and
`--path-as-root frontend` against those two services. Concurrency group `deploy-production`, never
cancelled mid-flight.

**This is not how production ships today.** The live topology (`DEPLOY.md`) is one Railway
service built from GitHub with Root Directory = `backend` (Railway's own integration redeploys it
on a push to `main`), plus the frontend on **Vercel** (Root Directory = `frontend`, its own GitHub
integration) — not a Railway frontend service, which `deploy.yml` assumes. The database is
Supabase, not Railway Postgres. Whether `RAILWAY_TOKEN` is set in the repo is not visible from the
code; if it ever is, the frontend step would need a Railway frontend service that does not exist.

## Voice evals — `.github/workflows/evals.yml`

Replays the voice agent against the real model: on a schedule (`17 3 * * 0-4`, 03:17 UTC Sunday to
Thursday, because scenarios say "morgen" and Friday/Saturday's tomorrow is a closed day) and on
`workflow_dispatch` (inputs `tag`, default `smoke`, and `repeat`, default `3` — a scenario passes on
a majority). **Never on push or PR**: a model's coin-flip must not decide a merge; `ci.yml` stays
the gate. One job, `replay`, with a two-entry matrix — `knowledge-base` (base prompt over the
default knowledge file) and `studio` (`--config app/workloads/conversational/evals/studio_agent.json`,
the prompt every Studio-published agent runs). It runs
`python -m app.workloads.conversational.evals.harness --run --repeat … --tag … --reports runs/evals/<agent>`
and uploads `backend/runs/evals/<agent>` as artifact `voice-evals-<agent>`.

Needs the repository secret **`OPENAI_API_KEY`**. Without it the first step (run from the repo
root, before checkout) warns and every later step is skipped, so the run is green, not red.

## Schema and templates on deploy — `backend/railway.json` and the app's startup

Two mechanisms, both idempotent, so either one alone keeps the database current.

**On startup.** `app/main.py`'s `lifespan` calls `bring_database_up_to_date()`
(`app/db/bootstrap.py`): `alembic upgrade head`, then seeding the runbook templates, before the
first request is served — and a failure stops the app from serving rather than letting it answer
against the wrong schema. Gated on `MIGRATE_ON_START` (default true; `app/config.py`). The same
lifespan starts the transcript retention sweep beside the app.

**Pre-deploy.** `deploy.preDeployCommand` runs `alembic upgrade head && python scripts/seed.py` in the built image
before traffic switches to the new container. So a migration and a new runbook version ship with
the code that needs them, with **no manual step and no CLI** — which matters because forgetting
either is invisible: the app starts fine and then fails on the first request that touches the new
column, or keeps running the old template version while the catalog shows the new one.

It is a *pre-deploy* step rather than the container's `CMD` on purpose: a stalled database fails
the deploy and leaves the old container serving, instead of hanging uvicorn on every boot. Both
commands are idempotent — alembic skips applied revisions, the seed skips any `(name, version)`
already present — so they are a no-op on a deploy that changes neither.

**Cron services.** `backend/railway.scheduler-near.json` (`python -m app.sync.scheduler near`,
`*/20 * * * *`) and `backend/railway.scheduler-horizon.json` (`… horizon`, `0 3 * * *`) define the
mirror's two Railway cron services from the same Dockerfile (`DEPLOY.md` §1b, ADR-0010).

**Instances still pin their template version** (ADR-0002 #15), so publishing v(n+1) does not move a
running instance: redeploy it in the studio, which offers that as a one-click update.

## What CI does **not** cover yet

- `mypy` (dev dependency is present; not run in CI until the type surface is stabilized).
- Frontend lint (`npm run lint` = `next lint` exists in `package.json`, but there is no ESLint
  config in the repo and CI does not run it).
- Frontend unit tests (none in the repo).
- End-to-end / browser tests (no surface for them yet).
- Model-driven voice behaviour in the merge gate — deliberately; it is the nightly `evals.yml`.
- The production database in deploy (Supabase; migrations there run at app startup and as the
  `preDeployCommand` above, not from CI).