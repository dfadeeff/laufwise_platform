"""The assess step against the database (ADR-0042): held, assessed beside the app, resumed, and
taken over after a crash once its lease has run out."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from app.assess import service as assess
from app.db.models import CaseRun
from app.db.session import get_sessionmaker
from tests.test_assess import CONTENT
from tests.test_builder import _mine, client, workspace  # noqa: F401
from tests.test_definition_versions import _in_db, seeded  # noqa: F401
from tests.test_workflow_agents import db

ASSESSED = {"total": "3.00", "basis": []}


class Scripted:
    model = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def assess(self, system, text, documents, tools, form, call):
        self.calls += 1
        return ASSESSED, [{"tool": "add", "arguments": {"a": "1", "b": "2"}, "result": await call("add", {"a": "1", "b": "2"})}]


def _published_assessing_definition() -> str:
    key = _mine()["key"]
    content = {k: copy.deepcopy(v) for k, v in CONTENT.items() if k != "references"}
    content["tests"] = [{
        "name": "probe", "source": "by hand", "input": {"note": "x", "assessment": ASSESSED},
        "expect": {"status": "ready", "result": {"charged": {"value": "3.00", "ref": "the assessment's total"}}},
        "counter": [{"no_letter_matches": "bedingt", "why": "no hedge"}],
    }]
    assert client.put(f"/api/v1/definitions/{key}/draft", json={"content": content}).status_code == 200
    published = client.post(f"/api/v1/definitions/{key}/publish")
    assert published.status_code == 200, published.text
    return key


def _computed_case(key: str) -> dict:
    agent = client.post("/api/v1/workflow-agents", json={"workflow": key, "name": "Probe"}).json()
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "t"}).json()
    case = client.get(f"/api/v1/cases/{case['id']}").json()
    saved = client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": case["input_revision"], "input": {"note": "x"}})
    assert saved.status_code == 200, saved.text
    computed = client.post(f"/api/v1/cases/{case['id']}/compute")
    assert computed.status_code == 200, computed.text
    return computed.json()


def _sweep(model) -> int:
    return _in_db(lambda _s: assess.sweep(get_sessionmaker(), model))


def _claim(case_id: str, minutes_ago: int) -> None:
    async def go(session):
        from sqlalchemy import select

        run = (await session.execute(select(CaseRun).where(CaseRun.case_id == case_id).order_by(CaseRun.started_at.desc()))).scalars().first()
        claimed = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        run.outputs = {**run.outputs, "assess": {**run.outputs["assess"], "claimed_at": claimed}}
        await session.commit()

    _in_db(go)


@db
def test_a_held_case_is_assessed_beside_the_app_and_comes_out_ready(workspace) -> None:  # noqa: F811
    key = _published_assessing_definition()
    case = _computed_case(key)
    assert case["status"] == "assessing" and case["result"] is None

    model = Scripted()
    _sweep(model)
    done = client.get(f"/api/v1/cases/{case['id']}").json()
    assert (done["status"], done["result"]) == ("ready", {"charged": "3.00"})
    assert model.calls == 1


@db
def test_after_a_crash_the_assessment_is_taken_over_once_its_lease_has_run_out(workspace) -> None:  # noqa: F811
    key = _published_assessing_definition()
    case = _computed_case(key)

    _claim(case["id"], minutes_ago=1)  # another worker holds it
    model = Scripted()
    _sweep(model)
    assert model.calls == 0 and client.get(f"/api/v1/cases/{case['id']}").json()["status"] == "assessing"

    _claim(case["id"], minutes_ago=20)  # that worker died: its lease ran out
    _sweep(model)
    assert model.calls == 1 and client.get(f"/api/v1/cases/{case['id']}").json()["status"] == "ready"
