# Deploying laufwise (Railway + Vercel + Clerk + Supabase)

Live topology: **1 Railway service** (FastAPI backend) · **1 Vercel project** (Next.js frontend) ·
**1 Clerk production instance** · the **existing Supabase** Postgres (unchanged).

The backend build is defined by `backend/Dockerfile` + `backend/railway.json` (installs the
laufwise engine from git, runs `alembic upgrade head` at start, serves uvicorn on `$PORT`).

## 1. Railway — the backend service

1. **New Project → Deploy from GitHub repo** → authorize Railway on GitHub → pick this repo.
   (If the repo isn't listed: the "Configure GitHub App" link lets you grant Railway access to it.)
2. In the service **Settings → Source**, set **Root Directory = `backend`** so Railway uses
   `backend/Dockerfile` and `backend/railway.json`.
3. **Variables** (Settings → Variables):
   | Key | Value |
   |---|---|
   | `DATABASE_URL` | Supabase **Session pooler** URL — `postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:5432/postgres` (IPv4; the direct `db.<ref>.supabase.co:5432` is IPv6-only and Railway can't reach it). Use the **Session** pooler (5432), NOT the Transaction pooler (6543): the engine uses NullPool with no `statement_cache_size=0`, so asyncpg's prepared statements break on the transaction pooler. |
   | `CONNECTION_ENC_KEY` | your existing Fernet key — must match, it decrypts stored credentials |
   | `CLERK_SECRET_KEY` | `sk_live_…` |
   | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | `pk_live_…` (backend derives the JWT issuer from it) |
   | `CORS_ORIGINS` | the Vercel URL, e.g. `https://laufwise.vercel.app` |
4. Deploy. Note the public URL (Settings → Networking → Generate Domain), e.g.
   `https://laufwise-api.up.railway.app`.

## 2. Clerk — go to production

Dev keys (`pk_test_`/`sk_test_`) only work on localhost. In the Clerk dashboard, **Create
production instance** (or "Deploy to production"). Using Vercel's default domain, register the
`*.vercel.app` URL as the production app URL. Copy the two production keys:
- `pk_live_…` → both Vercel and Railway (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`)
- `sk_live_…` → Railway (`CLERK_SECRET_KEY`)

## 3. Vercel — the frontend

1. **Add New → Project** → import this repo → **Root Directory = `frontend`** (Next.js auto-detected).
2. **Environment Variables**:
   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | the Railway backend URL (no trailing slash) |
   | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | `pk_live_…` |
3. Deploy. Then set `CORS_ORIGINS` on Railway to this Vercel URL (step 1.3) and redeploy the
   backend so it accepts the frontend's requests.

## Notes

- **Migrations** run automatically on each backend deploy (`alembic upgrade head`, idempotent).
- **Seeding templates is MANUAL** — unlike migrations, `runbooks/*.yaml` do NOT auto-publish; the
  app never seeds on boot. After adding a template or bumping a template's `version:`, run it
  deliberately (skip-if-exists, so a version bump is required to publish a change):
  ```
  # Railway (DATABASE_URL already = session pooler):
  railway run python scripts/seed.py
  # Local (direct host is IPv6-only — pass the IPv4 transaction pooler):
  DATABASE_URL="postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:6543/postgres" \
      python scripts/seed.py   # run from backend/
  ```
- **Background import jobs** run in-process threads (ADR-0004 D4a). A Railway redeploy mid-import
  orphans a job as `running`; because the import is idempotent + append-only, just re-run it.
- Never commit `.env`; all secrets live in the Railway/Vercel dashboards.
