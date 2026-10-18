# ADR-0024 — Mail is sent by the platform; a workspace only says how it appears

Status: accepted · 18 October 2026 · applies ADR-0022 D1 (kernel vs workspace) to email

## Context

The first live call's summary was never delivered: the deployment pointed at Gmail's SMTP server
without a login, and the From address was the practice's own domain, so Gmail refused with 530.
The fix was not more environment variables per customer. Railway holds the kernel; a customer's
settings belong in the Studio (ADR-0022).

## Decision

### D1 — The transport is the kernel's

`app/notifications/mail.py`: one sending service for the platform (Brevo's SMTP relay, an EU
processor), configured in the deployment (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_FROM`). Mail always goes out *from* the platform's verified address. A host
without `SMTP_FROM` counts as not configured, and activating an agent is refused until both are set.

### D2 — The identity is the workspace's

`AgentConfig.notification_sender_name` and `notification_reply_to`, beside `recipients` in
Phone & handoff: a mail reads "Praxis Podo · Mia <benachrichtigung@laufwise.com>" and a reply goes
to the practice. Empty: the practice's name and its `email`.

### D3 — A workspace's own mailbox, later, through one seam

`mail.transport_for(workspace)` is the only place a transport is chosen; it returns the platform's
today. A workspace that wants mail from its own address will bind an email connection (SMTP, or a
Google/Microsoft sign-in), stored encrypted like any connection, and `transport_for` returns that.
Nothing that sends mail changes.

## Consequences

- Practices set up nothing to receive summaries; the platform's domain is verified once (SPF,
  DKIM, DMARC in Brevo).
- Summaries hold patient names, so the mail processor needs an AVV/DPA (spec §7); Brevo is in the
  EU and offers one.
- Until `SMTP_FROM` is set, no summary is sent and no agent can be (re)activated; the reason shows
  in the History as `smtp_not_configured`.
