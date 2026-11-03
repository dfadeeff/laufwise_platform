"""Workflow definitions as versioned data (ADR-0039 step 2b), against the database.

Seeded from files at boot; a published version never changes (the database refuses it); a draft
becomes the newest published version and is offered from then on; a case records the exact
version that computed it, and keeps it when a newer one is published.

A published version can never be deleted, so tests that publish use keys of their own.
"""

from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.definitions import service as definitions
from app.kernel import modules
from app.kernel.definitions import DefinitionError, checksum, read_definition
from app.main import app
from tests.conftest import FIXTURE_DEFINITIONS
from tests.test_workflow_agents import SETTINGS, _as, _cleanup, _fixture, _PROPERTY, client, db

DEMO = FIXTURE_DEFINITIONS / "demo"


def _in_db(fn):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _demo_content(key: str) -> dict:
    content = copy.deepcopy(read_definition(DEMO / "umlage", DEMO))
    return {**content, "key": key, "label": f"Umlage {key}"}


@pytest.fixture
def seeded():
    """The fixture definitions seeded into the database, and the registry loaded from it."""
    _in_db(definitions.seed_from_files)
    _in_db(definitions.load_published)
    yield


@db
def test_seeding_publishes_once_and_refuses_a_changed_version_without_a_bump(seeded, tmp_path) -> None:
    assert _in_db(definitions.seed_from_files) == []  # already current: a restart is a no-op

    # The same files, one function edited, the version not bumped.
    changed = tmp_path / "defs"
    content = read_definition(DEMO / "umlage", DEMO)
    definitions.export_files({**content, "sources": {**content["sources"], "functions/check.py": "def run(*a):\n    return []\n"}}, changed)
    (changed / "demo" / "module.yaml").write_text((DEMO / "module.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(definitions.DefinitionConflict, match="bump `version`"):
        _in_db(lambda s: definitions.seed_from_files(s, changed))


@db
def test_the_database_refuses_to_change_or_delete_a_published_version(seeded) -> None:
    async def tamper(session: AsyncSession, statement: str):
        definition = await repo.get_definition(session, "umlage")
        await session.execute(sa.text(statement), {"id": definition.id})

    for statement in (
        "UPDATE definition_version SET content = '{}' WHERE definition_id = :id",
        "DELETE FROM definition_version WHERE definition_id = :id",
    ):
        with pytest.raises(Exception, match="is published and cannot change"):
            _in_db(lambda s, st=statement: tamper(s, st))


@db
def test_the_registry_offers_what_is_published_with_its_version(seeded) -> None:
    _, umlage = modules.workflow("umlage")
    assert umlage.version == 2 and umlage.gate["passed"] and umlage.version_id is not None
    assert umlage.rules_version == "demo rules v1"


@db
def test_a_draft_is_refused_until_it_can_run_and_is_then_offered_as_the_next_version(seeded) -> None:
    key = f"probe-{uuid.uuid4().hex[:8]}"
    first = _in_db(lambda s: definitions.save_draft(s, key, "demo", _demo_content(key)))
    assert (first.version, first.status) == (1, "draft")
    _in_db(lambda s: definitions.publish(s, key))
    assert modules.workflow(key)[1].version == 1

    # The next draft: version 2. Saving again replaces it; there is only ever one.
    broken = {**_demo_content(key), "functions": ["compute"]}
    assert _in_db(lambda s: definitions.save_draft(s, key, "demo", broken)).version == 2
    with pytest.raises(DefinitionError, match="no check function"):
        _in_db(lambda s: definitions.publish(s, key))
    assert modules.workflow(key)[1].version == 1  # still offered: the refused draft is not

    fixed = {**_demo_content(key), "label": "Umlage, korrigiert"}
    again = _in_db(lambda s: definitions.save_draft(s, key, "demo", fixed))
    assert again.version == 2
    _in_db(lambda s: definitions.publish(s, key))
    assert modules.workflow(key)[1].version == 2 and modules.workflow(key)[1].label == "Umlage, korrigiert"


@db
def test_a_case_keeps_the_version_that_computed_it_when_a_newer_one_is_published(seeded) -> None:
    key = f"probe-{uuid.uuid4().hex[:8]}"
    _in_db(lambda s: definitions.save_draft(s, key, "demo", _demo_content(key)))
    _in_db(lambda s: definitions.publish(s, key))
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "demo")
    try:
        agent = client.post("/api/v1/workflow-agents", json={"workflow": key, "name": "A"}).json()
        client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
        case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "T"}).json()
        client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("u2")})
        before = client.post(f"/api/v1/cases/{case['id']}/compute").json()
        v1 = modules.workflow(key)[1].version_id
        assert before["status"] == "ready" and before["definition_version_id"] == v1
        assert before["events"][-1]["definition_version"] == 1

        _in_db(lambda s: definitions.save_draft(s, key, "demo", {**_demo_content(key), "label": "v2"}))
        _in_db(lambda s: definitions.publish(s, key))

        kept = client.get(f"/api/v1/cases/{case['id']}").json()
        assert kept["definition_version_id"] == v1  # not moved by the newer version
        after = client.post(f"/api/v1/cases/{case['id']}/compute").json()
        assert after["definition_version_id"] == modules.workflow(key)[1].version_id != v1
    finally:
        app.dependency_overrides.pop(current_tenant, None)
        _cleanup()


def test_a_version_exported_to_files_reads_back_to_the_same_content(tmp_path) -> None:
    content = read_definition(DEMO / "umlage", DEMO)
    directory = definitions.export_files(content, tmp_path)
    assert checksum(read_definition(directory, directory.parent)) == checksum(content)
