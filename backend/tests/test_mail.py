"""Mail the platform sends for a workspace: the kernel's transport, the workspace's identity
(ADR-0024)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.config import AgentConfig
from app.notifications import mail
from app.workloads.conversational.notifications import send_call_summary

_PRACTICE = dict(
    practice_name="Praxis Podo", street="Baumkirchner Straße 19", postcode="81673", city="München",
    phone="+498941115335", email="muenchen@healthyfeet-podologie.de", recipients=["team@example.org"],
)
_SUMMARY = {"call_id": "c1", "at": "2026-10-18 16:20", "outcome": "NEUER TERMIN",
            "patient_name": "Max Buchmann", "run_ids": [], "staff_action_required": False}


@pytest.fixture
def platform(monkeypatch):
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp-relay.brevo.com")
    monkeypatch.setattr(mail.settings, "smtp_from", "benachrichtigung@laufwise.com")
    sent: list = []
    monkeypatch.setattr(mail, "deliver", lambda transport, message: sent.append((transport, message)))
    return sent


@pytest.mark.anyio
async def test_the_practice_is_the_name_and_the_reply_to_never_the_sender(platform) -> None:
    """Sending as the practice's own domain is what Gmail refused with 530 on 18 October."""
    delivery = await send_call_summary(_SUMMARY, practice=AgentConfig(**_PRACTICE).to_practice())

    (transport, message), = platform
    assert delivery["sent"] is True
    assert message["From"] == "Praxis Podo <benachrichtigung@laufwise.com>"
    assert message["Reply-To"] == "muenchen@healthyfeet-podologie.de"
    assert message["To"] == "team@example.org"
    assert transport.host == "smtp-relay.brevo.com"


@pytest.mark.anyio
async def test_the_workspace_chooses_how_it_appears(platform) -> None:
    config = AgentConfig(
        **_PRACTICE, notification_sender_name="Praxis Podo · Mia",
        notification_reply_to="empfang@example.org",
    )

    await send_call_summary(_SUMMARY, practice=config.to_practice())

    (_transport, message), = platform
    assert message["From"] == "Praxis Podo · Mia <benachrichtigung@laufwise.com>"
    assert message["Reply-To"] == "empfang@example.org"


@pytest.mark.anyio
async def test_a_host_without_the_platform_s_own_address_is_not_a_configured_sender(monkeypatch) -> None:
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(mail.settings, "smtp_from", None)

    delivery = await send_call_summary(_SUMMARY, practice=AgentConfig(**_PRACTICE).to_practice())

    assert mail.platform_transport() is None
    assert delivery["sent"] is False and delivery["reason"] == "smtp_not_configured"


def test_a_reply_to_must_be_an_address() -> None:
    with pytest.raises(ValidationError, match="reply-to"):
        AgentConfig(notification_reply_to="Praxis Podo")
