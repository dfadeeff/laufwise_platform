import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CalendarSystem, ConnectionSummary } from "@/types";
import type { AgentConfig, StudioAgent } from "./types";
import { Section, Field } from "./Fields";
export function PhoneSetup({
  agent,
  config,
  change,
  updated,
}: {
  agent: StudioAgent;
  config: AgentConfig;
  change: (patch: Partial<AgentConfig>) => void;
  updated: (agent: StudioAgent) => void;
}) {
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [systems, setSystems] = useState<CalendarSystem[]>([]);
  const [connection, setConnection] = useState(
    agent.channel?.connection_id ?? "",
  );
  const [number, setNumber] = useState(agent.channel?.phone_number ?? "");
  const [revision, setRevision] = useState(agent.published_instance_id ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    // Any account on a system the voice registry knows; which systems that is comes from the
    // backend, so a newly supported one appears here without a frontend change.
    Promise.all([api.listConnections(), api.listCalendarSystems()])
      .then(([rows, available]) => {
        setSystems(available);
        setConnections(rows.filter((c) => available.some((s) => s.key === c.adapter)));
      })
      .catch((e) => setError(e.message));
  }, []);
  async function action(kind: "check" | "activate" | "pause") {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (kind === "check")
        setMessage(
          (await api.checkAgentConnection(agent.id, connection)).message,
        );
      else {
        updated(
          kind === "pause"
            ? await api.pauseAgent(agent.id)
            : await api.activateAgent(agent.id, revision, connection, number),
        );
        setMessage(
          kind === "pause"
            ? "Phone answering paused."
            : "Published revision activated for this number.",
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Section
        title="Calendar connection"
        description="Select the practice system account and the calendar labels this agent works with."
      >
        <Field label="Practice system account">
          <select
            className="studio-input"
            value={connection}
            onChange={(e) => {
              setConnection(e.target.value);
              setMessage("");
            }}
          >
            <option value="">Select a connection</option>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {[c.label, systems.find((s) => s.key === c.adapter)?.label ?? c.adapter, c.id.slice(0, 6)]
                  .filter(Boolean)
                  .join(" · ")}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Bookable calendar labels"
          hint="Comma-separated labels. These must match the mapping in Connections."
        >
          <input
            className="studio-input"
            defaultValue={config.resources.join(", ")}
            onBlur={(e) =>
              change({
                resources: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
        </Field>
        <div className="flex flex-wrap items-center gap-4">
          <button
            disabled={busy || !connection}
            className="studio-secondary"
            onClick={() => action("check")}
          >
            Check saved configuration
          </button>
          <Link className="text-sm text-primary" href="/studio/governance/connections">
            Manage connections →
          </Link>
        </div>
      </Section>
      <Section
        title="Staff handoff"
        description="Your team receives call summaries and requests for help. Rehearsals never send these emails."
      >
        <Field
          label="Notification emails"
          hint="Separate multiple addresses with commas."
        >
          <input
            className="studio-input"
            defaultValue={config.recipients.join(", ")}
            onBlur={(e) =>
              change({
                recipients: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
        </Field>
        <Field
          label="Transfer calls to"
          hint="A caller who asks for a person is put through to this number during opening hours, e.g. +4989123456. Leave it empty to take callbacks instead."
        >
          <input
            className="studio-input"
            inputMode="tel"
            placeholder="+4989123456"
            defaultValue={config.transfer_number}
            onBlur={(e) =>
              change({ transfer_number: e.target.value.replace(/[\s()-]/g, "") })
            }
          />
        </Field>
      </Section>
      <Section
        title="Connect your phone"
        description="Publishing saves a revision. Activating below selects which published revision answers this number."
      >
        <Field label="Published revision">
          <select
            className="studio-input"
            value={revision}
            onChange={(e) => setRevision(e.target.value)}
          >
            <option value="">Publish a revision first</option>
            {agent.history.map((r) => (
              <option value={r.id} key={r.id}>
                Revision {r.revision} ·{" "}
                {new Date(r.created_at).toLocaleString()}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Twilio phone number"
          hint="Use a voice-enabled number in the platform’s configured Twilio account."
        >
          <input
            className="studio-input"
            placeholder="+493012345678"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
          />
        </Field>
        <div className="rounded-lg bg-muted/60 p-4 text-sm leading-6">
          <p className="font-medium">Set the incoming-call webhook in Twilio</p>
          <p className="mt-2 break-all">
            HTTP POST · {api._baseUrl}/telephony/incoming
          </p>
          <p className="mt-2 text-muted-foreground">
            The public backend must support secure WebSockets. Forwarding an
            existing practice line is configured with your phone provider.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            disabled={busy || !revision || !connection || !number}
            className="studio-primary"
            onClick={() => action("activate")}
          >
            {busy
              ? "Checking…"
              : agent.channel?.active
                ? "Switch live revision"
                : "Check & activate"}
          </button>
          {agent.channel?.active && (
            <button
              disabled={busy}
              className="studio-secondary"
              onClick={() => action("pause")}
            >
              Pause answering
            </button>
          )}
        </div>
      </Section>
      {config.recall_policy !== "off" && (
        <Section
          title="Callers this agent remembers"
          description="It recognises a number a verified patient has called from before, and forgets anyone it has not heard from in 180 days."
        >
          <button
            disabled={busy}
            className="studio-secondary"
            onClick={async () => {
              if (!window.confirm("Forget every caller this agent remembers?")) return;
              setBusy(true);
              setError("");
              try {
                const { forgotten } = await api.forgetCallers(agent.id);
                setMessage(
                  forgotten
                    ? `Forgotten ${forgotten} caller${forgotten === 1 ? "" : "s"}. They will be greeted as first-time callers.`
                    : "There was nobody to forget.",
                );
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Forget everyone
          </button>
        </Section>
      )}
      {message && (
        <p
          role="status"
          className="rounded-lg bg-success/10 p-4 text-sm text-success"
        >
          {message}
        </p>
      )}
      {error && (
        <p
          role="alert"
          className="rounded-lg bg-danger/10 p-4 text-sm text-danger"
        >
          {error}
        </p>
      )}
    </>
  );
}
