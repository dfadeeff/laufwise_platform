"""Bring the database up to date with the code that is starting (schema, then templates).

The deploy target runs whatever is in the image and nothing else: a migration or a new runbook
version that needs a separate manual command does not happen, and the failure is *invisible* —
the app starts fine and then errors on the first request touching the new column, or keeps serving
the old template version while the catalog advertises the new one. Both were observed in
production. So the code that needs the schema is the code that applies it.

Both steps are idempotent — alembic skips applied revisions, the seed skips any `(name, version)`
already present — so a restart that changes neither is a no-op, and a crash-loop cannot corrupt
anything by repeating them.

Failing here **stops the app from serving** on purpose. An API answering requests against a schema
it was not written for produces wrong answers rather than errors, and this platform's whole claim
is that its actions are grounded in real state. A container that refuses to start is loud; one that
serves a half-migrated database is not.

Deliberately not built: an advisory lock around the migration. Alembic itself takes one per
transaction, and the service runs a single replica; if it is ever scaled out, that assumption is
what needs revisiting first.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings
from app.db.seed import seed_templates_from_dir
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

# backend/ — alembic.ini and migrations/ live next to the app package.
_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_to_head() -> None:
    """Blocking: `alembic upgrade head`. Runs in a worker thread — `migrations/env.py` calls
    `asyncio.run()`, which raises if invoked from inside the already-running event loop."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    command.upgrade(config, "head")


async def bring_database_up_to_date() -> None:
    """Apply pending migrations, then publish any runbook version not yet in the catalog."""
    if not settings.migrate_on_start:
        logger.info("startup migration disabled (MIGRATE_ON_START=false)")
        return

    await asyncio.to_thread(_upgrade_to_head)
    logger.info("database schema is at head")

    async with get_sessionmaker()() as session:
        inserted = await seed_templates_from_dir(session, settings.templates_dir)
    logger.info(
        "templates published: %s", ", ".join(inserted) if inserted else "none (already current)"
    )
