"""A case held for a person's judgment (ADR-0039 step 3c), through the API and against the
database.

The demo case u4 has a disputed item: whether Frau Berg is charged for the extra cleaning is a
person's judgment. There are no figures until someone decides it, choosing one of the answers the
definition allows and saying why; who decided is recorded. The judgment cannot be typed into the
input form, only withdrawn there.
"""

from __future__ import annotations

import pytest

from tests.test_approvals import EMAILS, _request, client, outbox  # noqa: F401
from tests.test_letters import SETTINGS
from tests.test_workflow_agents import _fixture, db

# Mail goes to the outbox; the workspace is the demo property's, and Alice is signed in.
pytestmark = pytest.mark.usefixtures("outbox")

WAIVE = {"disputed": {"value": "waive", "reason": "Die Sonderreinigung war nicht beauftragt."}}


def _deciding_case() -> dict:
    agent = client.post("/api/v1/workflow-agents", json={"workflow": "umlage", "name": "D"}).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "D"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("u4")})
    return client.post(f"/api/v1/cases/{case['id']}/compute").json()


def _decide(case: dict, decisions: dict, revision: int | None = None):
    return client.post(
        f"/api/v1/cases/{case['id']}/decisions",
        json={"revision": revision or case["input_revision"], "decisions": decisions},
    )


def _save(case: dict, data: dict):
    return client.put(
        f"/api/v1/cases/{case['id']}/input", json={"revision": case["input_revision"], "input": data}
    )


@db
def test_a_case_held_for_a_judgment_shows_the_question_and_no_figures() -> None:
    case = _deciding_case()

    assert case["status"] == "deciding"
    assert case["result"] is None and case["drafts"] == []
    (asked,) = case["run"]["deciding"]
    assert asked["field"] == "disputed" and [o["value"] for o in asked["options"]] == ["charge", "waive"]
    assert case["run"]["waiting_for"] == [] and case["run"]["expires_at"] is None
    assert _request(case, EMAILS).status_code == 409  # nothing to send


@db
@pytest.mark.parametrize(
    "decisions",
    [
        {"title": {"value": "x", "reason": "weil"}},  # not a judgment it is held for
        {"disputed": {"value": "maybe", "reason": "weil"}},  # not an answer it allows
        {"disputed": {"value": "waive", "reason": "  "}},  # no reason
        {},
    ],
)
def test_a_decision_is_one_of_the_answers_allowed_with_a_reason(decisions) -> None:
    case = _deciding_case()
    assert _decide(case, decisions).status_code in (409, 422)
    assert client.get(f"/api/v1/cases/{case['id']}").json()["status"] == "deciding"


@db
def test_a_stale_decision_is_refused() -> None:
    case = _deciding_case()
    assert _decide(case, WAIVE, revision=1).status_code == 409


@db
def test_deciding_computes_the_case_again_and_records_who_and_why() -> None:
    case = _deciding_case()
    done = _decide(case, WAIVE).json()

    assert done["status"] == "ready" and done["input"]["disputed"] == "waive"
    assert done["run"]["id"] == case["run"]["id"] and done["run"]["state"] == "succeeded"
    totals = {s["party"]: s["total"] for s in done["result"]["statements"]}
    assert totals["Frau Berg"] == "778.61"
    (resumed,) = [e for e in done["events"] if e["kind"] == "resumed"]
    assert resumed["decisions"] == {
        "disputed": {"value": "waive", "reason": "Die Sonderreinigung war nicht beauftragt.", "by": "user_alice"}
    }


@db
def test_a_judgment_is_not_typed_into_the_input_only_withdrawn_there() -> None:
    case = _deciding_case()
    typed = _save(case, {**case["input"], "disputed": "charge"})
    assert typed.status_code == 409 and "judgment" in typed.json()["detail"]

    done = _decide(case, WAIVE).json()
    assert _save(done, {**done["input"], "disputed": "charge"}).status_code == 409
    kept = _save(done, {**done["input"], "title": "Treppenhaus 2025 (korrigiert)"})
    assert kept.status_code == 200  # the decision stands as it was
    withdrawn = _save(kept.json(), {**kept.json()["input"], "disputed": None}).json()
    assert withdrawn["input"]["disputed"] is None
    again = client.post(f"/api/v1/cases/{case['id']}/compute").json()
    assert again["status"] == "deciding"
