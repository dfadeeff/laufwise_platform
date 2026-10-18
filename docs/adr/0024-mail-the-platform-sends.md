# ADR-0024 — Mail is sent by the platform; a workspace only says how it appears

Status: accepted · 18 October 2026 · applies ADR-0022 D1 (kernel vs workspace) to email

## Context

The first live call's summary was never delivered: the deployment pointed at Gmail's SMTP server
without a login, and the From address was the practice's own domain, so Gmail refused with 530.
The fix was not more environment variables per customer. Railway holds the kernel; a customer's
settings belong in the Studio (ADR-0022).

## Decision

### D1 — The transport is the kernel's

`app/notifications/mail.py`: one sending service for the platform (any SMTP relay; Resend in production since 18 October 2026), configured in the deployment (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `SMTP_FROM`). Mail always goes out *from* the platform's verified address. A host
without `SMTP_FROM` counts as not configured, and activating an agent is refused until both are set.

### D2 — The identity is the workspace's

`AgentConfig.notification_sender_name` and `notification_reply_to`, beside `recipients` in
Phone & handoff: a mail reads "Praxis Podo · Mia <benachrichtigung@laufwise.com>" and a reply goes
to the practice. Empty: the practice's name and its `email`.

### D3 — A workspace can send from its own mail account, set up in the Studio

Connections → **Email sending** → "Connect your mail account": server, port, sender address,
login, password (presets for common providers). It is an `email` connection (`adapter="smtp"`),
the password encrypted like any connection's; the login is proven before anything is stored, and
"Send test email" sends one mail through it. One per workspace: connecting another removes the
previous one.

`mail.transport_for(session, tenant_id)` is the only place a transport is chosen: the workspace's
account if it has one, the platform's otherwise. A phone call asks for it when its summary is sent,
after the caller has hung up, so the lookup never delays a greeting and a database hiccup falls back
to the platform's sender instead of costing the call. Activation accepts either transport. A
Google or Microsoft sign-in instead of a password is a later way to create the same connection.

## Consequences

- Practices set up nothing to receive summaries unless they want their own sender; the platform's
  sending domain is verified once with its provider (SPF, DKIM).
- Summaries hold patient names, so the mail processor needs an AVV/DPA (spec §7). Resend stores
  data in the US; an EU provider is the answer before real patients (open question).
- With neither the platform's sender (`SMTP_HOST` + `SMTP_FROM`) nor a workspace account, no summary
  is sent and no agent can be (re)activated; the reason shows in the History as `smtp_not_configured`.
