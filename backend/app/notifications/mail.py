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

`transport_for` is the seam for a workspace's own mailbox (ADR-0024 D3, later): it will return
that connection's transport when one is bound, and the platform's otherwise. Nothing that sends
mail chooses a transport itself.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

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
    if not settings.smtp_host or not settings.smtp_from:
        return None
    return Transport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_address=settings.smtp_from,
        username=settings.smtp_username,
        password=settings.smtp_password,
        starttls=settings.smtp_starttls,
    )


def transport_for(workspace_id: object | None = None) -> Transport | None:
    """The transport a workspace's mail goes out through. Today always the platform's."""
    return platform_transport()


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
