"""A workspace's own documents for its workflow definitions (ADR-0041 step 2; ADR-0017).

A person chooses which of the workspace's documents a definition works from: a lease, a WEG
decision. The draft carries their text as it stands; a published version keeps the copy it was
published with; the builder reads them on every run; a document a draft still uses is not deleted.
"""

from __future__ import annotations

import uuid

from app.builder.service import _first_turn
from app.db.models import BuilderRun
from tests.test_builder import Scripted, _ask, _mine, client, workspace  # noqa: F401
from tests.test_definition_versions import seeded  # noqa: F401
from tests.test_workflow_agents import db

LEASE = "Mietvertrag Teichmann, § 4: Umgelegt werden Grundsteuer, Wasser, Heizung und Müll."


def _document(title: str = "Mietvertrag Teichmann", content: str = LEASE) -> str:
    res = client.post("/api/v1/knowledge", json={"title": title, "content": content})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _choose(key: str, ids: list[str]):
    return client.put(f"/api/v1/definitions/{key}/draft/knowledge", json={"ids": ids})


@db
def test_a_person_chooses_the_documents_a_draft_works_from(workspace) -> None:  # noqa: F811
    key = _mine()["key"]
    doc = _document()
    assert _choose(key, [doc]).status_code == 200

    (copy,) = client.get(f"/api/v1/definitions/{key}").json()["draft"]["content"]["knowledge"]
    assert (copy["id"], copy["title"], copy["content"]) == (doc, "Mietvertrag Teichmann", LEASE)
    assert len(copy["sha"]) == 12

    assert _choose(key, []).status_code == 200
    assert "knowledge" not in client.get(f"/api/v1/definitions/{key}").json()["draft"]["content"]


@db
def test_a_published_version_keeps_the_text_it_was_published_with(workspace) -> None:  # noqa: F811
    key = _mine()["key"]
    doc = _document()
    _choose(key, [doc])
    # Edited after it was chosen: publishing takes the text as it stands then.
    client.put(f"/api/v1/knowledge/{doc}", json={"title": "Mietvertrag Teichmann", "content": LEASE + " Stand 2026."})
    assert client.post(f"/api/v1/definitions/{key}/publish").status_code == 200

    client.put(f"/api/v1/knowledge/{doc}", json={"title": "Mietvertrag Teichmann", "content": "Ganz anders."})
    (pinned,) = client.get(f"/api/v1/definitions/{key}").json()["published"]["content"]["knowledge"]
    assert pinned["content"] == LEASE + " Stand 2026."


@db
def test_a_document_of_another_workspace_or_none_is_refused(workspace) -> None:  # noqa: F811
    key = _mine()["key"]
    refused = _choose(key, [uuid.uuid4().hex])
    assert refused.status_code == 409 and "not a document of this workspace" in refused.json()["detail"]


@db
def test_a_document_a_draft_works_from_is_not_deleted(workspace) -> None:  # noqa: F811
    mine = _mine()
    doc = _document()
    _choose(mine["key"], [doc])
    refused = client.delete(f"/api/v1/knowledge/{doc}")
    assert refused.status_code == 409 and mine["label"] in refused.json()["detail"]
    _choose(mine["key"], [])
    assert client.delete(f"/api/v1/knowledge/{doc}").status_code == 200


@db
def test_the_builder_reads_the_documents_as_they_stand_on_every_run(workspace) -> None:  # noqa: F811
    key = _mine()["key"]
    doc = _document()
    _choose(key, [doc])
    client.put(f"/api/v1/knowledge/{doc}", json={"title": "Mietvertrag Teichmann", "content": LEASE + " Neu."})
    workspace["model"] = model = Scripted({"summary": "x", "set": {"label": "Umlage (neu)"}})
    _ask(key)
    (run,) = client.get(f"/api/v1/definitions/{key}/builder").json()
    assert run["state"] == "succeeded"
    first = model.seen[0][1][0]["text"]
    assert "1 document of this workspace" in first and LEASE not in first.split("as JSON:\n", 1)[1]
    (copy,) = client.get(f"/api/v1/definitions/{key}").json()["draft"]["content"]["knowledge"]
    assert copy["content"] == LEASE + " Neu."


def test_the_workspaces_documents_come_after_the_law_and_before_what_a_person_attached() -> None:
    content = {
        "label": "x", "references": [{"name": "betrkv", "title": "BetrKV", "stand": "2023", "cites": ["BetrKV"], "text": "§ 1"}],
        "knowledge": [{"id": "a" * 32, "title": "Mietvertrag Teichmann", "sha": "0" * 12, "content": LEASE}],
    }
    turn = _first_turn(BuilderRun(instruction="NK-03", file_ids=[]), content, [])
    assert [d.name for d in turn["documents"]] == ["BetrKV (Stand: 2023)", "Mietvertrag Teichmann"]
    assert turn["documents"][1].data == LEASE.encode()
    assert '"knowledge"' not in turn["text"].split("as JSON:\n", 1)[1]
