# ADR 0001 — Persistence stack

- **Status:** Accepted (2026-06-28) · **amended 2026-10-18:** built as decided, but auth is
  **Clerk**, not Supabase Auth (ADR-0003 D2), and the "never PHI" line has drifted — see
  Implementation status.
- **Deciders:** project owner + grilling session

## Context
The platform needs durable, multi-tenant storage for app data (tenants, agent instances,
versioned runbooks, connections, runs). The backend currently has no database — `runtime.py`
is a stub and the console renders a static fixture. The choice gates the domain model,
identity/tenant scoping, and the later Temporal execution tier.

## Decision
Use **SQLAlchemy 2.0 (async) + asyncpg + Alembic**, pointed at a **dedicated Supabase
Postgres project** (region **EU** for GDPR/healthcare data residency).

- ORM models live in `backend/app/db/`; API DTOs stay separate in `backend/app/schemas/`
  (preserve the existing thin-DTO convention).
- Alembic for versioned migrations; migrations use the **direct** connection string (5432),
  not the pooler (6543).
- This Supabase project holds **app data + auth only — never PHI**.

## Consequences
- Standard, async, FastAPI-native data layer; keeps the ORM/DTO separation intact.
- Supabase gives Auth + RLS + dashboard without locking us into its client SDK.
- Temporal (later) will use its **own** Postgres (Temporal Cloud), not this project.
- Rejected: Supabase Python client / PostgREST (weak typing, awkward joins, fights the
  Temporal path); SQLModel (blurs ORM/DTO line, weaker async maturity); defer-DB (postpones
  the foundation and makes tenant isolation impossible to do for real).

## Implementation status (2026-10-18)

- **Built as decided:** `sqlalchemy[asyncio]` + `asyncpg` + `alembic` (`backend/pyproject.toml`);
  ORM in `backend/app/db/models.py`, queries in `backend/app/db/repo.py`, DTOs in
  `backend/app/schemas/`. The direct 5432 URL is derived in `backend/app/config.py`
  (`sqlalchemy_url`); migrations live in `backend/migrations/` (`env.py` + `versions/`).
- **Diverged:** the engine uses `NullPool` (`backend/app/db/session.py`) — a fresh asyncpg
  connection per checkout, not a pool. Auth is Clerk (`backend/app/auth/clerk.py`); Supabase holds
  app data only. No Postgres RLS: isolation is `tenant_id` filtering in `repo.py`.
- **Drift against "never PHI":** the app database now holds some patient-identifying data:
  `import_job.patients` and `import_job.review` (patient names for failed/forced/stale rows,
  ADR-0004/0005 import review), `caller_memory.patient_id` + `display_name` (ADR-0011 (caller
  recall); number hashed, no DOB), and call transcripts in `conversation_event`
  (`backend/app/workloads/conversational/recording.py`, with retention in `retention.py`). Each was
  a later decision; this ADR's rule has not been formally revised to match.
- **Not built:** Temporal (no dependency; `LocalEngine` only).
