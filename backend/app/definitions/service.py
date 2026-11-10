"""Workflow definitions as versioned data (ADR-0039 step 2b): no FastAPI.

- `seed_from_files`: the shipped definitions, published at boot. A published version never
  changes, so files whose content differs from the stored version with the same number stop the
  boot: bump `version` in definition.yaml to publish a change.
- `load_published`: the registry the routes read, from what is published.
- `workflow_of_version`: the exact version a run started on, to resume it on (step 3b).
- `save_draft` / `publish`: the lifecycle the Studio and the builder drive. A draft may
  change; publishing seals it (the database refuses any later change, migration b7c1d2e3f4a5),
  and only after every case passes (`app.definitions.gate`).
- `create_from_template` / `test_draft` / `discard_draft`: a workspace's own definitions (step
  5). A workspace copies a shipped template into a private draft, edits it, runs its cases, and
  publishes it; only that workspace sees it. A shipped template is never edited in place.
- `export_files`: a version back to the files it can be read from again.
"""

from __future__ import annotations

import copy
import functools
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.knowledge import pinned
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
    with_module_references,
    workflow_from,
)
from app.definitions.gate import require, run_gate
from app.kernel.workflows import Workflow

logger = logging.getLogger(__name__)


class DefinitionConflict(DefinitionError):
    """What is asked contradicts what is stored: a published version with other content, or a
    key that belongs to another module or another workspace."""


class NotYours(DefinitionError):
    """The definition is a shipped template, or another workspace's: it cannot be changed here."""


async def _definition(
    session: AsyncSession, key: str, module_key: str, tenant_id: uuid.UUID | None = None
) -> Definition:
    definition = await repo.get_definition(session, key, lock=True)
    if definition is None:
        definition = Definition(key=key, module_key=module_key, tenant_id=tenant_id)
        session.add(definition)
        await session.flush()
    elif definition.module_key != module_key:
        raise DefinitionConflict(f"definition {key!r} belongs to module {definition.module_key!r}")
    elif definition.tenant_id != tenant_id:
        raise NotYours(f"definition {key!r} is not this workspace's to change")
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
            # Published only if every case passes (ADR-0039 step 4): a failing one stops the boot,
            # as content that cannot run does.
            report = require(workflow_from(content, bundles_dir()), content)
            session.add(
                DefinitionVersion(
                    definition_id=definition.id,
                    version=version,
                    status="published",
                    content=content,
                    checksum=digest,
                    gate=report,
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
        workflow = workflow_from(
            version.content, bundles, version_id=str(version.id), gate=version.gate,
            owner=str(definition.tenant_id) if definition.tenant_id else None,
        )
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


async def create_from_template(
    session: AsyncSession, tenant_id: uuid.UUID, template_key: str, label: str
) -> DefinitionVersion:
    """A workspace's own copy of a shipped template, as a private draft (version 1). Its key is
    new, so the workspace's agents and cases never point at the template's."""
    template = await repo.get_definition(session, template_key)
    published = await repo.latest_published_of(session, template.id) if template else None
    if template is None or template.tenant_id is not None or published is None:
        raise DefinitionError(f"no template {template_key!r}")
    key = f"{template_key}-{secrets.token_hex(3)}"
    definition = Definition(key=key, module_key=template.module_key, tenant_id=tenant_id, template_key=template_key)
    session.add(definition)
    await session.flush()
    content = {**copy.deepcopy(published.content), "key": key, "label": label, "version": 1}
    draft = DefinitionVersion(definition_id=definition.id, version=1, status="draft", content=content, checksum=checksum(content))
    session.add(draft)
    await session.commit()
    return draft


async def with_workspace_knowledge(session: AsyncSession, content: dict, tenant_id: uuid.UUID | None) -> dict:
    """`content` with the text of each workspace document it names, as the document stands now
    (ADR-0041 step 2; ADR-0017 D2). A draft is refreshed at every save, builder run and publish;
    a published version keeps the copy it was published with."""
    if "knowledge" not in content:
        return content
    ids = [d.get("id") for d in content["knowledge"] if isinstance(d, dict)]
    found = await repo.knowledge_by_ids(session, tenant_id, ids) if tenant_id is not None else []
    if len(found) != len(content["knowledge"]):
        missing = sorted(set(map(str, ids)) - {d.id.hex for d in found})
        raise DefinitionError(f"not a document of this workspace: {', '.join(missing) or 'an entry without an id'}")
    return {**content, "knowledge": [pinned(d) for d in found]}


async def set_draft_knowledge(session: AsyncSession, key: str, ids: list[str], tenant_id: uuid.UUID) -> DefinitionVersion:
    """The workspace documents the definition's draft works from, chosen by a person, in order."""
    definition = await repo.get_definition(session, key)
    if definition is None or definition.tenant_id != tenant_id:
        raise NotYours(f"definition {key!r} is not this workspace's to change")
    base = await repo.definition_draft(session, definition.id) or await repo.latest_published_of(session, definition.id)
    content = {k: v for k, v in base.content.items() if k != "knowledge"}
    if ids:
        content["knowledge"] = [{"id": i} for i in ids]
    return await save_draft(session, key, definition.module_key, content, tenant_id)


async def definitions_using_document(session: AsyncSession, tenant_id: uuid.UUID, document_id: str) -> list[str]:
    """Labels of the workspace's definitions whose draft works from this document."""
    using = []
    for definition in await repo.definitions_of(session, tenant_id):
        draft = await repo.definition_draft(session, definition.id)
        if draft and any(d.get("id") == document_id for d in draft.content.get("knowledge") or []):
            using.append(draft.content.get("label") or definition.key)
    return using


async def save_draft(
    session: AsyncSession, key: str, module_key: str, content: dict, tenant_id: uuid.UUID | None = None,
    *, commit: bool = True,
) -> DefinitionVersion:
    """Create the definition's draft, or replace what it holds. A draft may be incomplete: it is
    checked when it is published. Its version is the next number. `tenant_id` must own it."""
    definition = await _definition(session, key, module_key, tenant_id)
    draft = await repo.definition_draft(session, definition.id)
    if draft is None:
        version = await repo.highest_definition_version(session, definition.id) + 1
        draft = DefinitionVersion(definition_id=definition.id, version=version, status="draft")
        session.add(draft)
    content = {**content, "key": key, "module": module_key, "version": draft.version}
    # The law is the module's: a draft carries the text it ships now (ADR-0041 D2), and the
    # workspace's documents as they stand now (step 2).
    content = with_module_references(content, modules.ROOT / module_key)
    content = await with_workspace_knowledge(session, content, definition.tenant_id)
    draft.content, draft.checksum = content, checksum(content)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return draft


async def publish(session: AsyncSession, key: str) -> DefinitionVersion:
    """Seal the definition's draft as its newest published version, and offer it from now on.
    Refused if the draft cannot run, or if any of its cases does not pass (the publish gate);
    then nothing changes, and the refusal says which case failed and why."""
    definition = await repo.get_definition(session, key, lock=True)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    if draft is None:
        raise DefinitionError(f"definition {key!r} has no draft to publish")
    # Published with the documents' text as it stands now, and kept so from then on.
    refreshed = await with_workspace_knowledge(session, draft.content, definition.tenant_id)
    if refreshed is not draft.content:
        draft.content, draft.checksum = refreshed, checksum(refreshed)
    check_content(draft.content)
    report = require(workflow_from(draft.content, bundles_dir()), draft.content)
    draft.status, draft.gate, draft.published_at = "published", report, datetime.now(timezone.utc)
    await session.commit()
    await load_published(session)
    return draft


async def test_draft(session: AsyncSession, key: str) -> dict:
    """Run the draft's cases as the gate would, without publishing. A draft that cannot run at all
    is reported, not raised: it is being edited."""
    definition = await repo.get_definition(session, key)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    if draft is None:
        raise DefinitionError(f"definition {key!r} has no draft")
    try:
        check_content(draft.content)
        return run_gate(workflow_from(draft.content, bundles_dir()), draft.content)
    except DefinitionError as exc:
        return {"at": datetime.now(timezone.utc).isoformat(), "cases": [], "passed": False, "refused": str(exc)}


async def confirm_figure(session: AsyncSession, key: str, case_name: str, path: str, by: str) -> DefinitionVersion:
    """A person has checked one expected figure against the source its case names: it counts
    from now on, and the draft records who confirmed it."""
    definition = await repo.get_definition(session, key, lock=True)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    content = copy.deepcopy(draft.content) if draft else {}
    case = next((c for c in content.get("tests") or [] if c.get("name") == case_name), None)
    figure = ((case or {}).get("expect") or {}).get("result", {}).get(path)
    if not isinstance(figure, dict) or not figure.get("unverified"):
        raise DefinitionError(f"{case_name}: {path} is not a figure waiting to be confirmed")
    figure.pop("unverified")
    figure["confirmed_by"] = by
    draft.content, draft.checksum = content, checksum(content)
    await session.commit()
    return draft


async def confirm_check(session: AsyncSession, key: str, by: str) -> DefinitionVersion:
    """A person has read what the builder changed in the check's code (the run's diff) and
    accepts it: the draft records who (ADR-0039 step 6f)."""
    definition = await repo.get_definition(session, key, lock=True)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    content = copy.deepcopy(draft.content) if draft else {}
    if not content.get("check_changed"):
        raise DefinitionError(f"{key}: no change to the check is waiting to be confirmed")
    content.pop("check_changed")
    content["check_confirmed_by"] = by
    draft.content, draft.checksum = content, checksum(content)
    await session.commit()
    return draft


async def discard_draft(session: AsyncSession, key: str) -> None:
    definition = await repo.get_definition(session, key, lock=True)
    draft = await repo.definition_draft(session, definition.id) if definition else None
    if draft is not None:
        await session.delete(draft)
        await session.flush()
        if not await repo.definition_versions(session, definition.id):
            await session.delete(definition)  # a copy never published: nothing of it is left
        await session.commit()


def export_files(content: dict, root: Path) -> Path:
    """Write a version's content as files under `root/<module>/`, readable again by
    `read_definition` to the same checksum: the runnable definition and the cases its publish
    gate runs. A definition's own pytest files and host-side helpers are not part of a version."""
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
    if "tests" in content:
        (directory / "cases").mkdir()
        for case in content["tests"]:
            body = {k: v for k, v in case.items() if k != "name"}
            (directory / "cases" / f"{case['name']}.yaml").write_text(
                yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
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
