"""A case that waits for a fact (ADR-0039 step 3b), through the API and against the database.

The demo case u3 waits for Firma Clausen's payment, which its bank confirms by 1 December. What
stands (Herr Adler's and Frau Berg's statements) is shown and can be sent meanwhile. Entering the
amount resumes the run; a letter already sent is not offered again. A change to the input ends the
held run, and a wait nobody attends ends at its date with a question for a person.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.workflow_agents import service
from tests.test_approvals import BOB, EMAILS, _as_person, _decide, _request, client, outbox  # noqa: F401
from tests.test_letters import SETTINGS
from tests.test_workflow_agents import _fixture, _run_db, db

# Mail goes to the outbox; the workspace is the demo property's, and Alice is signed in.
pytestmark = pytest.mark.usefixtures("outbox")


def _waiting_case() -> dict:
    agent = client.post("/api/v1/workflow-agents", json={"workflow": "umlage", "name": "W"}).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("u3")})
    return client.post(f"/api/v1/cases/{case['id']}/compute").json()


def _supply(case: dict, facts: dict, revision: int | None = None):
    return client.post(
        f"/api/v1/cases/{case['id']}/facts",
        json={"revision": revision or case["input_revision"], "facts": facts},
    )


AMOUNT = {"payments.2.amount": "100.00"}


@db
def test_a_waiting_case_shows_what_stands_and_what_it_waits_for() -> None:
    case = _waiting_case()

    assert case["status"] == "waiting"
    assert (case["run"]["state"], case["run"]["outcome"]) == ("suspended", None)
    assert case["run"]["waiting_for"] == [
        {"fact": "payments.2.amount", "what": "Zahlung von Firma Clausen", "from": "Sparkasse", "due": "2026-12-01"}
    ]
    assert case["run"]["expires_at"].startswith("2026-12-01T23:00:00")  # midnight in Germany
    assert [d["recipient"] for d in case["drafts"]] == ["Herr Adler", "Frau Berg"]
    pdf = client.get(f"/api/v1/cases/{case['id']}/letters/0/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


@db
def test_only_what_the_case_waits_for_is_entered_and_only_on_the_revision_read() -> None:
    case = _waiting_case()

    assert _supply(case, {"payments.0.amount": "1.00"}).status_code == 409  # not awaited
    assert _supply(case, {"payments.2.amount": ""}).status_code == 409
    assert _supply(case, AMOUNT, revision=1).status_code == 409  # stale
    again = client.get(f"/api/v1/cases/{case['id']}").json()
    assert again["status"] == "waiting" and again["input_revision"] == case["input_revision"]


@db
def test_the_fact_resumes_the_run_and_every_letter_is_offered() -> None:
    case = _waiting_case()
    done = _supply(case, AMOUNT).json()

    assert done["status"] == "ready" and done["input_revision"] == case["input_revision"] + 1
    assert done["computed_revision"] == done["input_revision"]
    assert done["run"]["id"] == case["run"]["id"]  # the same run, gone on
    assert (done["run"]["state"], done["run"]["outcome"]) == ("succeeded", "ready")
    assert done["input"]["payments"][2]["amount"] == "100.00"
    # Nothing was sent while it waited, so all three letters are offered.
    assert [d["recipient"] for d in done["drafts"]] == ["Herr Adler", "Frau Berg", "Firma Clausen"]
    resumed = [e for e in done["events"] if e["kind"] == "resumed"]
    assert [e["facts"] for e in resumed] == [{"payments.2.amount": {"value": "100.00", "from": "Sparkasse"}}]
    assert "facts_received" not in [e["kind"] for e in done["events"]]  # nothing is still missing


@db
def test_letters_sent_while_it_waited_are_not_offered_again(outbox) -> None:  # noqa: F811
    case = _waiting_case()
    asked = _request(case, {k: v for k, v in EMAILS.items() if k != "Firma Clausen"}).json()
    _as_person(BOB)  # Alice asked; Bob decides
    sent = _decide(asked["approval"]["id"], True).json()
    assert [d["status"] for d in sent["approval"]["deliveries"]] == ["sent", "sent"]

    done = _supply(sent, AMOUNT).json()
    assert done["status"] == "ready"
    assert [d["recipient"] for d in done["drafts"]] == ["Firma Clausen"]
    assert len(outbox) == 2


@db
def test_changing_the_input_of_a_waiting_case_ends_its_run() -> None:
    case = _waiting_case()
    saved = client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": _fixture("u2")},
    ).json()
    assert saved["status"] == "draft" and saved["run"]["state"] == "cancelled"
    assert _supply(saved, AMOUNT).status_code == 409  # nothing is awaited any more


@db
def test_a_wait_nobody_attends_ends_at_its_date_and_asks_a_person() -> None:
    case = _waiting_case()

    async def sweep(session, now):
        ended = await service.expire_waits(session, now)
        await session.commit()
        return ended

    assert _run_db(lambda s: sweep(s, datetime(2026, 12, 1, 22, 0, tzinfo=timezone.utc))) == 0
    assert _run_db(lambda s: sweep(s, datetime(2026, 12, 1, 23, 0, tzinfo=timezone.utc))) == 1

    expired = client.get(f"/api/v1/cases/{case['id']}").json()
    assert expired["status"] == "blocked" and expired["drafts"] == []
    assert expired["run"]["state"] == "expired" and expired["run"]["waiting_for"] == []
    assert "Sparkasse" in expired["questions"][0] and "01.12.2026" in expired["questions"][0]
    assert expired["events"][-1]["kind"] == "wait_expired"
    assert _supply(expired, AMOUNT).status_code == 409
