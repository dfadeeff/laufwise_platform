"""Mail the platform sends for a workspace: the kernel's transport, the workspace's identity
(ADR-0024)."""

from __future__ import annotations

import secrets

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


# --- a workspace's own mail account (ADR-0024 D3) ----------------------------------------------


# Generated per run: no password-like literal in the repository (secret scanners flag them).
_TEST_PASSWORD = secrets.token_urlsafe(12)


def _account(monkeypatch, *, port="587", security="starttls"):
    import json
    from types import SimpleNamespace

    from cryptography.fernet import Fernet

    from app.connections import crypto

    monkeypatch.setattr(crypto.settings, "connection_enc_key", Fernet.generate_key().decode())
    return SimpleNamespace(
        type="email",
        config={"host": "mail.privateemail.com", "port": port, "security": security,
                "from_address": "notifications@nebellabs.com"},
        tokens_enc=crypto.encrypt(json.dumps({"password": _TEST_PASSWORD})),
    )


def test_a_mail_account_becomes_a_transport_with_its_decrypted_password(monkeypatch) -> None:
    transport = mail.transport_of(_account(monkeypatch))

    assert (transport.host, transport.port, transport.starttls) == ("mail.privateemail.com", 587, True)
    assert transport.from_address == transport.username == "notifications@nebellabs.com"
    assert transport.password == _TEST_PASSWORD


@pytest.mark.anyio
async def test_a_workspace_with_its_own_account_sends_through_it(monkeypatch, platform) -> None:
    account = _account(monkeypatch)

    async def email_connection(_session, _tenant_id):
        return account

    monkeypatch.setattr("app.db.repo.email_connection", email_connection)
    import uuid

    transport = await mail.transport_for(object(), uuid.uuid4())
    await send_call_summary(_SUMMARY, practice=AgentConfig(**_PRACTICE).to_practice(), transport=transport)

    (used, message), = platform
    assert used.host == "mail.privateemail.com"
    assert message["From"] == "Praxis Podo <notifications@nebellabs.com>"


@pytest.mark.anyio
async def test_a_workspace_without_one_uses_the_platform_s(monkeypatch, platform) -> None:
    async def email_connection(_session, _tenant_id):
        return None

    monkeypatch.setattr("app.db.repo.email_connection", email_connection)
    import uuid

    transport = await mail.transport_for(object(), uuid.uuid4())

    assert transport is not None and transport.host == "smtp-relay.brevo.com"


def test_the_platform_sender_survives_a_pasted_space(monkeypatch) -> None:
    monkeypatch.setattr(mail.settings, "smtp_host", "smtp.resend.com")
    monkeypatch.setattr(mail.settings, "smtp_from", " notifications@nebellabs.com")

    assert mail.platform_transport().from_address == "notifications@nebellabs.com"


@pytest.mark.anyio
async def test_connecting_an_account_whose_login_fails_stores_nothing(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.v1 import connections

    def refuse(transport):
        raise OSError("535 authentication failed")

    monkeypatch.setattr(connections.mail, "check_login", refuse)

    with pytest.raises(HTTPException) as refused:
        await connections._verify_mail_account(
            {"host": "mail.privateemail.com", "port": "587", "from_address": "notifications@nebellabs.com"},
            {"password": "wrong"},
        )

    assert refused.value.status_code == 400 and "refused the login" in refused.value.detail


@pytest.mark.parametrize(
    ("config", "credentials", "says"),
    [
        ({"port": "587", "from_address": "a@b.de"}, {"password": "x"}, "mail server"),
        ({"host": "h", "from_address": "nope"}, {"password": "x"}, "sent from"),
        ({"host": "h", "port": "abc", "from_address": "a@b.de"}, {"password": "x"}, "number"),
        ({"host": "h", "from_address": "a@b.de"}, {}, "password"),
    ],
)
def test_a_mail_account_form_says_what_is_missing(config, credentials, says) -> None:
    from fastapi import HTTPException

    from app.api.v1 import connections

    with pytest.raises(HTTPException) as refused:
        connections._mail_transport(config, credentials)

    assert says in refused.value.detail
