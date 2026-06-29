"""Seed template contracts from filesystem YAML into the DB (idempotent).

Stage 1 authored templates as YAML; until the Stage-3 authoring API exists, this promotes those
YAML files into published `Template` rows so runs and the catalog can resolve them from Postgres.
Skips any (name, version) already present, so it is safe to run repeatedly.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Template
from app.templates.loader import load_template


async def seed_templates_from_dir(session: AsyncSession, templates_dir: str | Path) -> list[str]:
    """Upsert each *.yaml as a published template. Returns the names that were newly inserted."""
    inserted: list[str] = []
    for path in sorted(Path(templates_dir).glob("*.yaml")):
        contract = load_template(path)
        if await repo.get_template_version(session, contract.name, contract.version):
            continue
        session.add(
            Template(
                name=contract.name,
                version=contract.version,
                agent_class=contract.agent_class,
                agent_surface=contract.agent_surface,
                risk=contract.risk,
                status="published",
                contract=contract.model_dump(mode="json"),
                parameters={k: v.model_dump(mode="json") for k, v in contract.parameters.items()},
            )
        )
        inserted.append(contract.name)
    if inserted:
        await session.commit()
    return inserted