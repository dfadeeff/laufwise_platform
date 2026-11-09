"""A crash after SMTP accepts a letter must not permit a blind resend."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.approvals import service
from app.db import repo
from app.notifications.mail import Identity, Transport


def test_the_send_attempt_is_committed_before_smtp_and_an_unknown_outcome_is_not_retried(monkeypatch):
    letter = {"recipient": "Tenant", "email": "tenant@example.org", "title": "Settlement"}
    draft = {"recipient": "Tenant", "title": "Settlement", "text": "A letter", "enclosure": ""}
    case = SimpleNamespace(id="case", tenant_id="tenant", drafts=[draft], input_revision=1, result={})
    approval = SimpleNamespace(id="approval", status="approved", payload={"letters": [letter]}, deliveries=[])
    approval.fingerprint = service.fingerprint(case, approval.payload)
    events, smtp = [], []
    class Session:
        def add(self, row):
            pass
        async def commit(self):
            events.append([dict(d) for d in approval.deliveries])
        async def refresh(self, row):
            pass
    monkeypatch.setattr(repo, "get_workflow_case", AsyncMock(return_value=case))
    monkeypatch.setattr(service, "letter_pdf", lambda *a: b"PDF")
    monkeypatch.setattr(service, "_as_draft", lambda d: d)
    def interrupted(*a):
        smtp.append(True)
        assert events and events[-1][0]["status"] == "sending"
        raise ConnectionResetError("SMTP receipt lost after DATA")
    monkeypatch.setattr(service, "deliver", interrupted)
    session, transport = Session(), Transport("mail.example.org", 587, "sender@example.org")
    asyncio.run(service.send(session, case, approval, transport, Identity("Manager")))
    assert approval.deliveries[0]["status"] == "uncertain"
    with pytest.raises(service.Refused, match="reconcil"):
        asyncio.run(service.send(session, case, approval, transport, Identity("Manager")))
    assert len(smtp) == 1
