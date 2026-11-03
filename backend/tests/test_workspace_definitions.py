"""A workspace builds its own definition from a template (ADR-0039 step 5), against the database.

Workspace A copies the demo template into a private draft, edits it, runs its cases, and publishes
it through the gate; then creates an agent from it. Workspace B never sees it. A shipped template
is never edited in place, and a draft's code runs only in the sandbox.

The two workspaces are this file's own and are not deleted afterwards: a published version can
never be deleted (the database refuses), so neither can the workspace that owns it.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import Principal, current_principal, current_tenant
from app.main import app
from tests.test_definition_versions import seeded  # noqa: F401
from tests.test_workflow_agents import SETTINGS, _as, _fixture, db

client = TestClient(app)


@pytest.fixture
def workspaces(seeded):  # noqa: F811
    """Two fresh workspaces; requests go out as A's administrator until switched."""
    suffix = uuid.uuid4().hex[:8]
    a, b = f"org_test_defs_a_{suffix}", f"org_test_defs_b_{suffix}"
    app.dependency_overrides[current_tenant] = _as(a, "demo")
    app.dependency_overrides[current_principal] = lambda: Principal("user_ada", "org:admin")
    yield a, b
    app.dependency_overrides.pop(current_tenant, None)
    app.dependency_overrides.pop(current_principal, None)


def _as_workspace(org: str) -> None:
    app.dependency_overrides[current_tenant] = _as(org, "demo")


def _copy(label: str = "Umlage Lindenstraße") -> dict:
    res = client.post("/api/v1/definitions", json={"template": "umlage", "label": label})
    assert res.status_code == 201, res.text
    return client.get(f"/api/v1/definitions/{res.json()['key']}").json()


def _save(definition: dict, content: dict):
    return client.put(f"/api/v1/definitions/{definition['key']}/draft", json={"content": content})


@db
def test_a_workspace_copies_a_template_into_a_private_draft(workspaces) -> None:
    listed = client.get("/api/v1/definitions").json()
    assert "umlage" in [t["key"] for t in listed["templates"]] and listed["own"] == []

    mine = _copy()
    assert mine["key"].startswith("umlage-") and mine["owner"] == "workspace" and mine["template"] == "umlage"
    assert (mine["published"], mine["draft"]["version"]) == (None, 1)
    assert mine["draft"]["content"]["label"] == "Umlage Lindenstraße"
    assert [d["key"] for d in client.get("/api/v1/definitions").json()["own"]] == [mine["key"]]

    _as_workspace(workspaces[1])
    assert client.get(f"/api/v1/definitions/{mine['key']}").status_code == 404
    assert client.get("/api/v1/definitions").json()["own"] == []


@db
def test_a_shipped_template_is_read_but_never_changed_in_place(workspaces) -> None:
    template = client.get("/api/v1/definitions/umlage").json()
    assert template["owner"] == "platform" and template["published"]["gate"]["passed"]
    assert any(g["question"].startswith("Which tests") for g in template["guardrails"])
    assert _save(template, template["published"]["content"]).status_code == 403
    assert client.post("/api/v1/definitions/umlage/publish").status_code == 403
    assert client.post("/api/v1/definitions/umlage/draft/test").status_code == 403


@db
def test_a_draft_is_tested_without_publishing_and_published_only_when_its_cases_pass(workspaces) -> None:
    mine = _copy()
    content = copy.deepcopy(mine["draft"]["content"])
    u2 = next(c for c in content["tests"] if c["name"] == "u2")
    u2["expect"]["result"]["total"]["value"] = "1.00"
    assert _save(mine, content).status_code == 200

    report = client.post(f"/api/v1/definitions/{mine['key']}/draft/test").json()
    assert not report["passed"]
    assert report["cases"][0]["failures"] == ["total: 1940.90, expected 1.00 (1.048,47 + 778,61 + 113,82)"]
    assert client.get(f"/api/v1/definitions/{mine['key']}").json()["published"] is None

    app.dependency_overrides[current_principal] = lambda: Principal("user_max", "org:member")
    assert client.post(f"/api/v1/definitions/{mine['key']}/publish").status_code == 403
    app.dependency_overrides[current_principal] = lambda: Principal("user_ada", "org:admin")
    refused = client.post(f"/api/v1/definitions/{mine['key']}/publish")
    assert refused.status_code == 422 and not refused.json()["report"]["passed"]

    u2["expect"]["result"]["total"]["value"] = "1940.90"
    _save(mine, content)
    done = client.post(f"/api/v1/definitions/{mine['key']}/publish").json()
    assert done["version"] == 1 and done["gate"]["passed"]
    detail = client.get(f"/api/v1/definitions/{mine['key']}").json()
    assert detail["draft"] is None and detail["versions"] == [
        {"version": 1, "status": "published", "passed": True, "published_at": detail["published"]["published_at"]}
    ]


@db
def test_a_published_definition_is_this_workspaces_agent_and_no_other(workspaces) -> None:
    mine = _copy()
    assert client.post(f"/api/v1/definitions/{mine['key']}/publish").status_code == 200

    offered = {w["key"]: w for w in client.get("/api/v1/workflow-agents/workflows").json()}
    assert offered[mine["key"]]["own"] is True and offered["umlage"]["own"] is False
    agent = client.post("/api/v1/workflow-agents", json={"workflow": mine["key"], "name": "Meine Umlage"})
    assert agent.status_code == 201
    agent_id = agent.json()["id"]
    client.put(f"/api/v1/workflow-agents/{agent_id}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent_id}/cases", json={"title": "T"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("u2")})
    assert client.post(f"/api/v1/cases/{case['id']}/compute").json()["status"] == "ready"

    _as_workspace(workspaces[1])
    assert mine["key"] not in [w["key"] for w in client.get("/api/v1/workflow-agents/workflows").json()]
    assert client.post("/api/v1/workflow-agents", json={"workflow": mine["key"], "name": "X"}).status_code == 404


@db
def test_edited_code_runs_only_in_the_sandbox(workspaces) -> None:
    mine = _copy()
    content = copy.deepcopy(mine["draft"]["content"])
    content["sources"]["functions/compute.py"] = (
        "def run(input, settings, rules):\n    return {'result': {'leak': open('/etc/passwd').read()}}\n"
    )
    _save(mine, content)
    report = client.post(f"/api/v1/definitions/{mine['key']}/draft/test").json()

    assert not report["passed"]
    failures = " ".join(f for c in report["cases"] for f in c["failures"])
    assert "status failed" in failures and "root:" not in failures
    assert client.post(f"/api/v1/definitions/{mine['key']}/publish").status_code == 422


@db
def test_a_draft_that_cannot_run_is_reported_not_raised(workspaces) -> None:
    mine = _copy()
    content = {**copy.deepcopy(mine["draft"]["content"]), "functions": ["compute"]}
    _save(mine, content)
    report = client.post(f"/api/v1/definitions/{mine['key']}/draft/test").json()
    assert not report["passed"] and "no check function" in report["refused"]


@db
def test_discarding_a_copy_never_published_leaves_nothing(workspaces) -> None:
    mine = _copy()
    assert client.delete(f"/api/v1/definitions/{mine['key']}/draft").status_code == 204
    assert client.get(f"/api/v1/definitions/{mine['key']}").status_code == 404
    assert client.get("/api/v1/definitions").json()["own"] == []
