"""The approval gate on a case's letters (ADR-0033), against the database.

Alice (administrator) requests, Bob (administrator) decides, Carol (member) may not. What is
approved is fixed when it is requested; the requester never decides; approving re-checks the case
and sends each letter once, recording what the mail server said.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.api import deps
from app.api.deps import Principal, current_principal, current_tenant
from app.api.v1 import approvals as approval_routes
from app.api.v1 import workflow_agents as case_routes
from app.approvals import service as approvals
from app.config import settings
from app.db.models import ApprovalRequest
from app.main import app
from app.notifications.mail import Transport
from tests.test_letters import SETTINGS
from tests.test_workflow_agents import _as, _cleanup, _fixture, _PRACTICE, _PROPERTY, _run_db, db

client = TestClient(app)
ALICE, BOB, CAROL = (
    Principal("user_alice", "org:admin"),
    Principal("user_bob", "org:admin"),
    Principal("user_carol", "org:member"),
)
EMAILS = {"Herr Özdemir": "oezdemir@example.org", "Frau Lindqvist": "lindqvist@example.org"}


def _as_person(principal: Principal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


class Outbox(list):
    """The messages 'sent'; a recipient in `refuse` is refused by the 'mail server' instead."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse: set[str] = set()


@pytest.fixture
def outbox(monkeypatch):
    """Mail is configured and goes nowhere: every message lands here. A recipient listed in
    `outbox.refuse` is refused by the 'mail server' instead."""
    sent = Outbox()

    def deliver(transport, message):
        if message["To"] in sent.refuse:
            raise OSError("550 mailbox unavailable")
        sent.append(message)

    async def transport_for(session, tenant_id):
        return Transport("smtp.test", 587, "noreply@laufwise.test")

    monkeypatch.setattr(approvals, "deliver", deliver)
    monkeypatch.setattr(case_routes, "transport_for", transport_for)
    monkeypatch.setattr(approval_routes, "transport_for", transport_for)
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "immobilien")
    _as_person(ALICE)
    yield sent
    app.dependency_overrides.pop(current_tenant, None)
    app.dependency_overrides.pop(current_principal, None)
    _cleanup()


def _ready_case() -> dict:
    agent = client.post(
        "/api/v1/workflow-agents", json={"workflow": "betriebskosten", "name": "A"}
    ).json()
    client.put(f"/api/v1/workflow-agents/{agent['id']}/settings", json={"settings": SETTINGS})
    case = client.post(f"/api/v1/workflow-agents/{agent['id']}/cases", json={"title": "W5"}).json()
    client.put(f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("nk02")})
    return client.post(f"/api/v1/cases/{case['id']}/compute").json()


def _request(case: dict, emails: dict = EMAILS):
    return client.post(f"/api/v1/cases/{case['id']}/approval", json={"emails": emails})


def _decide(approval_id: str, approve: bool, note: str = ""):
    return client.post(
        f"/api/v1/approvals/{approval_id}/decide", json={"approve": approve, "note": note}
    )


@db
def test_a_second_administrator_approves_and_each_tenant_gets_their_letter(outbox) -> None:
    case = _ready_case()
    asked = _request(case).json()
    approval = asked["approval"]
    assert approval["status"] == "pending"
    assert approval["requested_by_me"] is True and approval["can_decide"] is False

    assert _decide(approval["id"], True).status_code == 403  # Alice asked: not hers to decide
    _as_person(CAROL)
    assert _decide(approval["id"], True).status_code == 403  # not an administrator
    assert outbox == []

    _as_person(BOB)
    assert client.get(f"/api/v1/cases/{case['id']}").json()["approval"]["can_decide"] is True
    done = _decide(approval["id"], True).json()

    assert done["approval"]["status"] == "approved"
    assert [d["status"] for d in done["approval"]["deliveries"]] == ["sent", "sent"]
    assert [m["To"] for m in outbox] == ["oezdemir@example.org", "lindqvist@example.org"]
    letter = outbox[0]
    assert letter["Subject"].startswith("Betriebskostenabrechnung 2025")
    assert "42,61 €" in letter.get_body().get_content()
    (attachment,) = list(letter.iter_attachments())
    assert attachment.get_filename() == "Einzelaufstellung Herr Özdemir.txt"
    kinds = [e["kind"] for e in done["events"]]
    assert kinds[-3:] == ["approval_requested", "approval_decided", "letters_sent"]


@db
def test_only_a_ready_case_with_an_address_for_every_letter_is_put_up(outbox) -> None:
    case = _ready_case()
    missing = _request(case, {"Herr Özdemir": "oezdemir@example.org"})
    assert missing.status_code == 422 and "Frau Lindqvist" in missing.json()["detail"]

    client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": _fixture("nk02")},
    )
    assert _request(case).status_code == 409  # saved, not computed: no letters


@db
def test_nothing_is_put_up_for_approval_while_mail_cannot_be_sent(outbox, monkeypatch) -> None:
    async def none(session, tenant_id):
        return None

    monkeypatch.setattr(case_routes, "transport_for", none)
    assert _request(_ready_case()).status_code == 503


@db
def test_a_rejection_needs_a_reason_and_the_case_can_be_put_up_again(outbox) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]
    _as_person(BOB)
    assert _decide(approval_id, False).status_code == 422
    rejected = _decide(approval_id, False, "Adresse von Frau Lindqvist prüfen").json()
    assert rejected["approval"]["status"] == "rejected" and outbox == []

    _as_person(ALICE)
    again = _request(case).json()["approval"]
    assert again["status"] == "pending" and again["id"] != approval_id


@db
def test_changing_the_case_withdraws_what_was_waiting(outbox) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]

    client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": _fixture("nk02")},
    )
    _as_person(BOB)
    refused = _decide(approval_id, True)

    assert refused.status_code == 409
    assert client.get(f"/api/v1/cases/{case['id']}").json()["approval"]["status"] == "withdrawn"
    assert outbox == []


@db
def test_an_expired_request_cannot_be_approved(outbox) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]

    async def expire(s):
        await s.execute(
            update(ApprovalRequest).values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await s.commit()

    _run_db(expire)
    _as_person(BOB)
    assert _decide(approval_id, True).status_code == 409
    assert client.get(f"/api/v1/cases/{case['id']}").json()["approval"]["status"] == "expired"


@db
def test_a_letter_the_mail_server_refused_is_sent_again_and_no_letter_twice(outbox) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]
    outbox.refuse.add("lindqvist@example.org")
    _as_person(BOB)

    first = _decide(approval_id, True).json()["approval"]["deliveries"]
    assert [(d["recipient"], d["status"]) for d in first] == [
        ("Herr Özdemir", "sent"),
        ("Frau Lindqvist", "failed"),
    ]
    assert "550" in first[1]["error"]

    outbox.refuse.clear()
    again = client.post(f"/api/v1/approvals/{approval_id}/send").json()["approval"]["deliveries"]
    assert [d["status"] for d in again] == ["sent", "sent"]
    assert [m["To"] for m in outbox] == ["oezdemir@example.org", "lindqvist@example.org"]


@db
def test_waiting_approvals_are_listed_for_the_workspace_and_nowhere_else(outbox) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]
    _as_person(BOB)
    (waiting,) = client.get("/api/v1/approvals").json()
    assert waiting["case"]["title"] == "W5" and waiting["can_decide"] is True

    app.dependency_overrides[current_tenant] = _as(_PRACTICE, "praxis")
    assert client.get("/api/v1/approvals").json() == []
    assert _decide(approval_id, True).status_code == 404


def test_without_a_token_production_knows_no_one(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(Exception) as refused:
        deps.current_principal(None)
    assert getattr(refused.value, "status_code", None) == 401


def test_the_fingerprint_changes_with_any_letter_address_or_figure() -> None:
    class Case:
        input_revision = 2
        result = {"total": "1.00"}
        drafts = [{"recipient": "A", "text": "x"}]

    base = approvals.fingerprint(Case, {"letters": [{"email": "a@b.de"}]})
    assert base != approvals.fingerprint(Case, {"letters": [{"email": "c@b.de"}]})
    Case.drafts = [{"recipient": "A", "text": "y"}]
    assert base != approvals.fingerprint(Case, {"letters": [{"email": "a@b.de"}]})


@db
def test_after_sending_a_corrected_settlement_can_be_sent_but_the_same_letters_cannot(
    outbox,
) -> None:
    case = _ready_case()
    approval_id = _request(case).json()["approval"]["id"]
    _as_person(BOB)
    _decide(approval_id, True)
    _as_person(ALICE)
    assert _request(case).status_code == 409  # the same letters, again

    corrected = {**_fixture("nk02")}
    corrected["tenancies"][1]["monthly_advance"] = "200.00"  # a correction
    saved = client.put(
        f"/api/v1/cases/{case['id']}/input",
        json={"revision": case["input_revision"], "input": corrected},
    ).json()
    recomputed = client.post(f"/api/v1/cases/{saved['id']}/compute").json()
    assert recomputed["approval"]["status"] == "approved"
    assert recomputed["approval"]["current"] is False  # it approved the earlier letters

    again = _request(recomputed).json()["approval"]
    assert again["status"] == "pending" and again["current"] is True
