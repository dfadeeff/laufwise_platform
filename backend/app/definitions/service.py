"""Workflow definitions as versioned data (ADR-0039 step 2b): no FastAPI.

- `seed_from_files`: the shipped definitions, published at boot. A published version never
  changes, so files whose content differs from the stored version with the same number stop the
  boot: bump `version` in definition.yaml to publish a change.
- `load_published`: the registry the routes read, from what is published.
- `workflow_of_version`: the exact version a run started on, to resume it on (step 3b).
- `save_draft` / `publish`: the lifecycle the Studio and the builder will drive. A draft may
  change; publishing seals it (the database refuses any later change, migration b7c1d2e3f4a5).
- `export_files`: a version back to the files it can be read from again.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Definition, DefinitionVersion
from app.kernel import modules
from app.kernel.definitions import (
    DefinitionError,
    bundles_dir,
    check_content,
    checksum,
    module_manifests,
    read_definitions,
    workflow_from,
)
from app.kernel.workflows import Workflow

logger = logging.getLogger(__name__)


class DefinitionConflict(DefinitionError):
    """What is asked contradicts what is stored: a published version with other content, or a
    key that belongs to another module."""


async def _definition(session: AsyncSession, key: str, module_key: str) -> Definition:
    definition = await repo.get_definition(session, key, lock=True)
    if definition is None:
        definition = Definition(key=key, module_key=module_key)
        session.add(definition)
        await session.flush()
    elif definition.module_key != module_key:
        raise DefinitionConflict(f"definition {key!r} belongs to module {definition.module_key!r}")
    return definition


async def seed_from_files(session: AsyncSession, root: Path | None = None) -> list[str]:
    """Publish every definition version under `root` that is not stored yet. Returns what was
    published, as "key vN"."""
    published = []
    for directory, _ in module_manifests(root or modules.ROOT):
        for content in read_definitions(directory):
            key, version = content["key"], content["version"]
            definition = await _definition(session, key, content["module"])
            stored = await repo.get_definition_version(session, definition.id, version)
            digest = checksum(content)
            if stored is not None:
                if stored.status != "published" or stored.checksum != digest:
                    raise DefinitionConflict(
                        f"{key} v{version} is stored with other content; published versions never "
                        f"change, so bump `version` in its definition.yaml to publish this one"
                    )
                continue
            session.add(
                DefinitionVersion(
                    definition_id=definition.id,
                    version=version,
                    status="published",
                    content=content,
                    checksum=digest,
                    published_at=datetime.now(timezone.utc),
                )
            )
            published.append(f"{key} v{version}")
    await session.commit()
    return published


async def load_published(session: AsyncSession) -> dict[str, tuple[Workflow, ...]]:
    """Fill the registry with each definition's newest published version."""
    bundles = bundles_dir()
    by_module: dict[str, list[Workflow]] = {}
    for definition, version in await repo.latest_published_versions(session):
        workflow = workflow_from(version.content, bundles, version_id=str(version.id))
        by_module.setdefault(definition.module_key, []).append(workflow)
    published = {k: tuple(v) for k, v in by_module.items()}
    modules.set_published(published)
    return published


@functools.cache
def _earlier_bundles() -> Path:
    return bundles_dir()


async def workflow_of_version(
    session: AsyncSession, version_id: uuid.UUID | None, current: Workflow
) -> Workflow:
    """The workflow a run started on. A held run resumes on that version even after a newer one
    was published: its first steps and its last were computed by the same rules."""
    if version_id is None or current.version_id == str(version_id):
        return current
    version = await session.get(DefinitionVersion, version_id)
    if version is None:
        raise DefinitionError(f"definition version {version_id} is not stored")
    return workflow_from(version.content, _earlier_bundles(), version_id=str(version.id))


async def save_draft(session: AsyncSession, key: str, module_key: str, content: dict) -> DefinitionVersion:
    """Create the definition's draft, or replace what it holds. A draft may be incomplete: it is
    checked when it is published. Its version is the next number."""
    definition = await _definition(session, key, module_key)
    draft = await repo.definition_draft(session, definition.id)
    if draft is None:
        version = await repo.highest_definition_version(session, definition.id) + 1
        draft = DefinitionVersion(definition_id=definition.id, version=version, status="draft")
        session.add(draft)
    content = {**content, "key": key, "module": module_key, "version": draft.version}
    draft.content, draft.checksum = content, checksum(content)
    await session.commit()
    return draft


async def publish(session: AsyncSession, key: str) -> DefinitionVersion:
    """Seal the definition's draft as its newest published version, and offer it from now on.
    Refused if the draft cannot run. (Step 4 adds: refused unless its tests pass.)"""
    definition = await repo.get_definition(session, key, lock=True)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    if draft is None:
        raise DefinitionError(f"definition {key!r} has no draft to publish")
    check_content(draft.content)
    workflow_from(draft.content, bundles_dir())  # it must build, not only validate
    draft.status, draft.published_at = "published", datetime.now(timezone.utc)
    await session.commit()
    await load_published(session)
    return draft


def export_files(content: dict, root: Path) -> Path:
    """Write a version's content as files under `root/<module>/`, readable again by
    `read_definition` to the same checksum. The runnable definition only: a definition's tests
    and host-side helpers are not part of a version."""
    module_dir = root / content["module"]
    directory = module_dir / content["key"]
    directory.mkdir(parents=True)
    spec = {
        "key": content["key"],
        "version": content["version"],
        "label": content["label"],
        "summary": content["summary"],
        "functions": content["functions"],
        "rules": {"file": "rules/rules.yaml", "name": content["rules_name"]},
    }
    if "procedure" in content:
        spec["procedure"] = content["procedure"]
    (directory / "definition.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (directory / "forms").mkdir()
    for name, form in content["forms"].items():
        (directory / "forms" / f"{name}.json").write_text(
            json.dumps(form, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (directory / "rules").mkdir()
    (directory / "rules" / "rules.yaml").write_text(
        yaml.safe_dump(content["rules"], allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if content["guide"]:
        (directory / "guide.md").write_text(content["guide"], encoding="utf-8")
    for path, text in content["sources"].items():
        target = module_dir / path if path.startswith("shared/") else directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory
