"""Email the platform sends on a workspace's behalf (ADR-0024).

Two layers, and the line between them is the point:

- **The transport is the kernel's.** One sending service for the whole platform (Brevo's SMTP
  relay in production), configured once in the deployment, sending from one verified address on
  the platform's own domain. A practice never configures SMTP, app passwords or DNS.
- **The identity is the workspace's.** Who the mail says it is from (the display name), where a
  reply goes, and who receives it are workspace settings, edited in the Studio.

So a mail reads "Praxis Podo · Mia <benachrichtigung@laufwise.com>", and replying writes to the
practice. Mail is never sent *as* the practice's own domain: that is what a mail server rightly
refuses (the 530 "Authentication Required" seen on 18 October 2026) and what spoofing looks like.

`transport_for` is the one place a transport is chosen: the workspace's own mail account when it
has connected one in the Studio (ADR-0024 D3, an `email` connection, its password encrypted like
any connection's), the platform's otherwise. Nothing that sends mail chooses a transport itself.
"""

from __future__ import annotations

import json
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from app.config import settings


@dataclass(frozen=True)
class Transport:
    """Where mail goes out, and the one address it is sent from."""

    host: str
    port: int
    from_address: str
    username: str | None = None
    password: str | None = None
    starttls: bool = True


@dataclass(frozen=True)
class Identity:
    """How a workspace appears in a mail it sends: a name, and where replies go."""

    name: str
    reply_to: str | None = None


def platform_transport() -> Transport | None:
    """The kernel's sending service, or None while it is not configured.

    Both a host and a From address are required: without a From of our own the only candidate is
    the customer's address, which is exactly the mail a provider refuses.
    """
    # Stripped: a value pasted with a leading space (" notifications@…") is not an address.
    host, sender = (settings.smtp_host or "").strip(), (settings.smtp_from or "").strip()
    if not host or not sender:
        return None
    return Transport(
        host=host,
        port=settings.smtp_port,
        from_address=sender,
        username=(settings.smtp_username or "").strip() or None,
        password=settings.smtp_password,
        starttls=settings.smtp_starttls,
    )


def transport_of(connection: Any) -> Transport:
    """A workspace's `email` connection as a transport. Its password is decrypted only here."""
    from app.connections import crypto

    config = connection.config or {}
    secret = json.loads(crypto.decrypt(connection.tokens_enc)) if connection.tokens_enc else {}
    return Transport(
        host=str(config["host"]),
        port=int(config.get("port") or 587),
        from_address=str(config["from_address"]),
        username=str(config.get("username") or config["from_address"]),
        password=secret.get("password"),
        starttls=config.get("security", "starttls") != "none",
    )


async def transport_for(session: Any, tenant_id: uuid.UUID | None) -> Transport | None:
    """The workspace's own mail account if it connected one, else the platform's."""
    if session is not None and tenant_id is not None:
        from app.db import repo

        connection = await repo.email_connection(session, tenant_id)
        if connection is not None:
            return transport_of(connection)
    return platform_transport()


def check_login(transport: Transport) -> None:
    """Prove the account accepts this login, sending nothing. Blocking; raises on refusal."""
    connect = smtplib.SMTP_SSL if transport.port == 465 else smtplib.SMTP
    with connect(transport.host, transport.port, timeout=15) as server:  # type: ignore[operator]
        if transport.port != 465 and transport.starttls:
            server.starttls()
        if transport.username and transport.password:
            server.login(transport.username, transport.password)


def compose(
    *, transport: Transport, identity: Identity, to: list[str], subject: str, body: str
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((identity.name, transport.from_address))
    message["To"] = ", ".join(to)
    if identity.reply_to:
        message["Reply-To"] = identity.reply_to
    message.set_content(body)
    return message


def deliver(transport: Transport, message: EmailMessage) -> None:
    """Blocking send. Call it in a worker thread, never on the event loop."""
    connect = smtplib.SMTP_SSL if transport.port == 465 else smtplib.SMTP
    with connect(transport.host, transport.port, timeout=20) as server:  # type: ignore[operator]
        if transport.port != 465 and transport.starttls:
            server.starttls()
        if transport.username and transport.password:
            server.login(transport.username, transport.password)
        server.send_message(message)
