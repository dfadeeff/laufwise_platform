"""Startup bootstrap: the image brings its own database up to date (app/db/bootstrap.py).

This exists because the failure it prevents is silent. A deploy whose migration never ran starts
cleanly and then errors on the first request that touches the new column; a deploy whose templates
were never seeded serves the previous contract version while the catalog advertises the new one.
Both happened in production before this code existed.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import bootstrap
from app.db.models import Template


def _run_db(fn):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                return await fn(s)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _db_reachable() -> bool:
    try:
        _run_db(lambda s: s.execute(select(1)))
        return True
    except Exception:
        return False


def test_bootstrap_can_be_switched_off(monkeypatch):
    """A process pointed at a database it must not touch (a replica, someone else's environment)
    has to be able to opt out — and opting out must not need the database to be reachable."""
    monkeypatch.setattr(settings, "migrate_on_start", False)

    def _explode() -> None:
        raise AssertionError("migration ran despite MIGRATE_ON_START=false")

    monkeypatch.setattr(bootstrap, "_upgrade_to_head", _explode)
    asyncio.run(bootstrap.bring_database_up_to_date())


@pytest.mark.skipif(not _db_reachable(), reason="Supabase DB not reachable")
def test_bootstrap_reaches_head_and_publishes_runbooks_and_repeats_cleanly():
    """Run it twice: the schema ends at head with the catalog carrying every runbook on disk, and
    the second run changes nothing. Restarts and crash-loops repeat this — it must be a no-op."""
    for _ in range(2):
        asyncio.run(bootstrap.bring_database_up_to_date())

    async def check(session: AsyncSession):
        applied = (
            await session.execute(
                text(
                    "select count(*) from information_schema.columns "
                    "where table_name = 'import_job' and column_name = 'forced'"
                )
            )
        ).scalar()
        versions = (
            await session.execute(
                select(Template.version).where(Template.name == "calendar_import")
            )
        ).scalars().all()
        return applied, versions

    applied, versions = _run_db(check)
    assert applied == 1, "the newest migration is applied"
    assert 3 in versions, "the runbook on disk is published"
    assert len(versions) == len(set(versions)), "a second run must not duplicate a version"
