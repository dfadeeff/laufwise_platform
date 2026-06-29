"""Thin repository helpers over the ORM — the queries the runtime and API need.

Kept deliberately small (CLAUDE.md §III): add a function when a caller needs it, not before.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import EpisodeEvent, Run, Template


async def list_template_names(session: AsyncSession) -> list[str]:
    stmt = (
        select(Template.name)
        .where(Template.status == "published")
        .distinct()
        .order_by(Template.name)
    )
    return list((await session.execute(stmt)).scalars().all())


async def latest_published_template(session: AsyncSession, name: str) -> Template | None:
    """The highest published version of a template by name (instances pin a specific version,
    but ad-hoc runs use the latest published)."""
    stmt = (
        select(Template)
        .where(Template.name == name, Template.status == "published")
        .order_by(Template.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_template_version(
    session: AsyncSession, name: str, version: int
) -> Template | None:
    stmt = select(Template).where(Template.name == name, Template.version == version)
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    template_name: str,
    template_version: int,
    status: str,
    trace_ref: str | None,
    step_payloads: list[dict[str, Any]],
    instance_id: uuid.UUID | None = None,
) -> Run:
    """Persist a finished run + its ordered engine events (one EpisodeEvent per step)."""
    run = Run(
        id=run_id,
        instance_id=instance_id,
        template_name=template_name,
        template_version=template_version,
        status=status,
        trace_ref=trace_ref,
    )
    run.events = [
        EpisodeEvent(seq=i, writer="engine", kind="step", payload=payload)
        for i, payload in enumerate(step_payloads)
    ]
    session.add(run)
    await session.commit()
    return run


async def list_runs(session: AsyncSession, limit: int = 50) -> list[Run]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> Run | None:
    """Fetch a run with its ordered episode events eager-loaded (async — no lazy loading)."""
    stmt = select(Run).where(Run.id == run_id).options(selectinload(Run.events))
    return (await session.execute(stmt)).scalar_one_or_none()