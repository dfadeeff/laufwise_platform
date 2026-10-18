"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ConnectionSummary } from "@/types";
import { Field, Section } from "@/features/agents/Fields";

/** Common providers, so a practice does not have to look up its mail server. */
const PROVIDERS: { name: string; host: string; port: string }[] = [
  { name: "Namecheap Private Email", host: "mail.privateemail.com", port: "587" },
  { name: "Google Workspace / Gmail (app password)", host: "smtp.gmail.com", port: "587" },
  { name: "Microsoft 365 / Outlook", host: "smtp.office365.com", port: "587" },
  { name: "IONOS", host: "smtp.ionos.de", port: "587" },
  { name: "STRATO", host: "smtp.strato.de", port: "465" },
];

/** Where the workspace's call summaries are sent from (ADR-0024).
 *
 *  Without a mail account the platform sends them; with one, they come from the practice's own
 *  address. The password is checked by logging in before anything is stored, and stays encrypted
 *  on the server — it is never shown again. */
export function EmailSending({
  account,
  onChanged,
}: {
  account: ConnectionSummary | null;
  onChanged: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("587");
  const [from, setFrom] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [testTo, setTestTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const connect = () =>
    run(async () => {
      await api.createConnection({
        type: "email",
        adapter: "smtp",
        credentials: { password },
        config: {
          label: from.trim(),
          host: host.trim(),
          port: port.trim(),
          from_address: from.trim(),
          username: (username || from).trim(),
        },
      });
      setPassword("");
      setOpen(false);
      // One account per workspace: the one it replaces goes, with its stored password.
      if (account) await api.removeConnection(account.id);
      await onChanged();
      return "Mail account connected. Send a test email to see it arrive.";
    });

  return (
    <Section
      title="Email sending"
      description="Call summaries and callback requests go to your team by email. They are sent by the platform, or from your practice's own address once you connect your mail account."
    >
      {account ? (
        <>
          <p className="text-sm">
            <span className="text-success">Sending from {account.email_from}</span>
            <span className="text-muted-foreground"> · via {account.email_host}</span>
          </p>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <Field label="Send a test email to">
              <input
                className="studio-input"
                type="email"
                placeholder="you@example.de"
                value={testTo}
                onChange={(e) => setTestTo(e.target.value)}
              />
            </Field>
            <button
              type="button"
              className="studio-secondary"
              disabled={busy || !testTo.trim()}
              onClick={() =>
                void run(async () => {
                  const sent = await api.sendTestEmail(account.id, testTo.trim());
                  return `Test email sent from ${sent.from} to ${sent.to}. Check the inbox, and the spam folder.`;
                })
              }
            >
              {busy ? "Sending…" : "Send test email"}
            </button>
          </div>
          <div className="flex flex-wrap gap-4">
            <button type="button" className="text-sm text-primary" onClick={() => setOpen(true)}>
              Change mail account →
            </button>
            <button
              type="button"
              className="text-sm text-danger"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api.removeConnection(account.id);
                  await onChanged();
                  return "Mail account removed. Summaries are sent by the platform again.";
                })
              }
            >
              Remove
            </button>
          </div>
        </>
      ) : (
        !open && (
          <div>
            <p className="text-sm text-muted-foreground">
              Summaries are currently sent by the platform. Connect your mail account to send them
              from your own address.
            </p>
            <button type="button" className="studio-primary mt-3" onClick={() => setOpen(true)}>
              Connect your mail account
            </button>
          </div>
        )
      )}
      {open && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void connect();
          }}
        >
          <Field label="Your provider" hint="Fills in the server. Not listed? Enter it yourself below.">
            <select
              className="studio-input"
              defaultValue=""
              onChange={(e) => {
                const provider = PROVIDERS.find((p) => p.host === e.target.value);
                if (provider) {
                  setHost(provider.host);
                  setPort(provider.port);
                }
              }}
            >
              <option value="">Choose…</option>
              {PROVIDERS.map((p) => (
                <option key={p.host} value={p.host}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
            <Field label="Mail server (SMTP)">
              <input className="studio-input" required value={host} onChange={(e) => setHost(e.target.value)} />
            </Field>
            <Field label="Port">
              <input className="studio-input" required inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} />
            </Field>
          </div>
          <Field label="Send from" hint="The address summaries come from, e.g. praxis@ihre-praxis.de.">
            <input className="studio-input" type="email" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="Login" hint="Usually the same address. Leave it empty if it is.">
            <input className="studio-input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder={from} />
          </Field>
          <Field
            label="Password"
            hint="Checked by logging in once, then stored encrypted. Gmail and Microsoft accounts need an app password."
          >
            <input
              className="studio-input"
              type="password"
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-3">
            <button className="studio-primary" disabled={busy}>
              {busy ? "Checking the login…" : "Connect mail account"}
            </button>
            <button type="button" className="studio-secondary" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {error && (
        <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger [overflow-wrap:anywhere]" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-lg bg-success/10 p-3 text-sm text-success" role="status">
          {notice}
        </p>
      )}
    </Section>
  );
}
