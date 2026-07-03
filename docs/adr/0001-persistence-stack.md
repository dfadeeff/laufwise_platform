# ADR 0001 — Persistence stack

- **Status:** Accepted (2026-06-28)
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
