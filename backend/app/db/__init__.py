"""Persistence layer (ADR-0001 stack: async SQLAlchemy 2.0 + asyncpg + Alembic, Supabase EU).

ORM models live here; API DTOs stay in `app/schemas` (the thin-DTO convention). This package
holds app data only — never PHI (ADR-0001 / ADR-0002 #11).
"""