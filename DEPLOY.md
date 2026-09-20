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
   | `DEEPGRAM_API_KEY` | Deepgram project key used by the Studio conversational tester |
   | `OPENAI_API_KEY` | OpenAI project key; the default voice LLM is `gpt-4.1-mini` |
   | `ELEVENLABS_API_KEY` | ElevenLabs API key used only by the backend voice pipeline |
   | `ELEVENLABS_VOICE_ID` | Default multilingual ElevenLabs voice ID used for every language without a specific override |
   | `ELEVENLABS_VOICE_ID_DE` | Optional native German voice override |
   | `ELEVENLABS_VOICE_ID_EN` | Optional native English voice override |
   | `ELEVENLABS_VOICE_ID_AR` | Optional native Arabic voice override |
   | `VOICE_STT_MODEL` | optional; defaults to `flux-general-multi` |
   | `VOICE_LLM_MODEL` | optional; defaults to `gpt-4.1-mini` |
   | `VOICE_TTS_MODEL` | optional; defaults to `eleven_flash_v2_5` |
4. Deploy. Note the public URL (Settings → Networking → Generate Domain), e.g.
   `https://laufwise-api.up.railway.app`.

## 1b. Railway — the mirror's clock (two cron services, ADR-0010)

The occupancy mirror used to run only when someone pressed the Studio button with the tab open.
These two services are its backend clock. Both are **the same image and the same repo** as the
backend — only the start command and the schedule differ.

| Service | Config file | Schedule (UTC) | Covers |
|---|---|---|---|
| `scheduler-near` | `backend/railway.scheduler-near.json` | `*/20 * * * *` | today … +7 days |
| `scheduler-horizon` | `backend/railway.scheduler-horizon.json` | `0 3 * * *` | the whole booking horizon |

1. **Add New → Empty Service** (twice), same repo, **Root Directory = `backend`**, and set each
   one's *Config as code* path to the file above.
2. Give both the **same variables as the backend service**: `DATABASE_URL`, `CONNECTION_ENC_KEY`
   (without it no connection credential can be decrypted), and any `*_BASE_URL` overrides. They do
   **not** need the Clerk, Deepgram, ElevenLabs or OpenAI keys — the clock makes no model calls and
   answers no requests.
3. **Do not give them `preDeployCommand`.** Migrations and seeding belong to the backend service;
   running them from three places races on deploy.
4. **Arm the instance.** The clock fires deployed instances whose `schedule` column is set:
   ```sql
   UPDATE agent_instance SET schedule = 'mirror' WHERE id = '<the availability_mirror instance>';
   ```
   `NULL` means manual-only, and pausing the instance disarms it without clearing the column.

Each run exits when it is done; nothing is held in memory between fires. A missed fire is repaired
by the next one, which is why there is no retry logic. Watch it work on
`/api/admin/belegung` on the practice's website — every day carries the time it was last mirrored.

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
- **Templates publish on deploy, not on boot.** `preDeployCommand` runs `python scripts/seed.py`
  alongside the migration, so `runbooks/*.yaml` reach the catalog with the deploy that carries
  them. The app still never seeds on boot (a boot-time DB step once hung the service), and the
  seed is skip-if-exists on `(name, version)` — **so a version bump is still required to publish a
  change to an existing template**. Run it by hand only when publishing without a deploy:
  ```
  # Railway (DATABASE_URL already = session pooler):
  railway run python scripts/seed.py
  # Local (direct host is IPv6-only — pass the IPv4 transaction pooler):
  DATABASE_URL="postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:6543/postgres" \
      python scripts/seed.py   # run from backend/
  ```
- **Background import jobs** run in-process threads (ADR-0004 D4a). A Railway redeploy mid-import
  orphans a job as `running`; because the import is idempotent + append-only, just re-run it. The
  scheduler reclaims such an orphan as `interrupted` after 30 minutes without progress, so it
  cannot block later runs (ADR-0010 D5).
- Never commit `.env`; all secrets live in the Railway/Vercel dashboards.
