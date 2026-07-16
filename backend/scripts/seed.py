"""Publish runbook templates (runbooks/*.yaml) into the DB. Idempotent, run deliberately.

Templates are NOT auto-published: the app never seeds on boot (no lifespan hook), and the
Railway start command is bare uvicorn on purpose (a boot-time DB step once hung the service —
see DEPLOY.md). So a new template, or a template whose `version:` you bumped, stays invisible in
Studio until this runs.

Skip-if-exists on (name, version): editing a YAML in place changes nothing. To publish a change
you must FIRST bump `version:` in the YAML — published versions are immutable, and instances pin
the version they were deployed with.

Connection — uses the app's configured DSN (`DATABASE_URL` if set, else the derived Supabase URL):

  - Local Mac: the derived DIRECT host is IPv6-only and won't route, so pass the IPv4 pooler:
      DATABASE_URL="postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:6543/postgres" \
          python scripts/seed.py
  - Railway (DATABASE_URL already set to the session pooler):
      railway run python scripts/seed.py

`statement_cache_size=0` keeps this working through the pgbouncer transaction pooler (:6543),
which rejects prepared statements; it's harmless on the session pooler / direct connection.

Run from the `backend/` directory so `./runbooks` resolves.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import repo
from app.db.seed import seed_templates_from_dir


async def main() -> None:
    engine = create_async_engine(
        settings.sqlalchemy_url,
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        inserted = await seed_templates_from_dir(session, settings.templates_dir)
        published = await repo.list_templates(session)
    await engine.dispose()

    print(f"newly published: {inserted or '(none — every (name, version) already present)'}")
    print("catalog now:")
    for t in published:
        print(f"  {t.name} v{t.version} [{t.status}]")


if __name__ == "__main__":
    asyncio.run(main())
