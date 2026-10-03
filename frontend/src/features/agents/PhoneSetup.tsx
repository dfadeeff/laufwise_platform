import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CalendarSystem, ConnectionSummary, NumbersView } from "@/types";
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
  const [numbers, setNumbers] = useState<NumbersView | null>(null);
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
        const usable = rows.filter((c) => available.some((s) => s.key === c.adapter));
        setConnections(usable);
        // One account with its calendars mapped is the only sensible choice: choose it, so the
        // practice is not left with an empty field and a greyed-out Activate it cannot explain.
        const mapped = usable.filter((c) => Object.keys(c.mapping ?? c.rooms ?? {}).length > 0);
        if (mapped.length === 1) setConnection((current) => current || mapped[0].id);
      })
      .catch((e) => setError(e.message));
    api
      .listNumbers()
      .then(setNumbers)
      .catch((e) => setError(e.message));
  }, []);
  async function changeNumbers(kind: "claim" | "release", target: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const next =
        kind === "claim" ? await api.claimNumber(target) : await api.releaseNumber(target);
      setNumbers(next);
      if (kind === "claim") setNumber(target);
      else if (number === target) setNumber("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
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
            placeholder="Your practice's own number, starting with +49"
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
          label="Phone number"
          hint="A number your practice claimed below. Its calls reach this agent as soon as it is activated."
        >
          <select
            className="studio-input"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
          >
            <option value="">
              {numbers?.owned.length ? "Select one of your numbers" : "Get a number below first"}
            </option>
            {numbers?.owned.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Field>
        <div className="rounded-lg border border-border p-4">
          <p className="text-sm font-medium">Get a number</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            Claim a number from the platform. It is wired to your agent automatically — nothing
            to set up with the phone provider. Your practice can hold up to {numbers?.max ?? 3}.
          </p>
          {numbers?.unavailable_reason ? (
            <p className="mt-3 text-sm text-warning">{numbers.unavailable_reason}</p>
          ) : numbers && numbers.available.length === 0 ? (
            <p className="mt-3 text-sm text-warning">
              No numbers are free right now. Ask your administrator to add numbers to the pool.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {numbers?.available.slice(0, 5).map((n) => (
                <li key={n.number} className="flex flex-wrap items-center justify-between gap-3">
                  <span className="text-sm">
                    {n.number}
                    {n.name && n.name !== n.number ? (
                      <span className="text-muted-foreground"> · {n.name}</span>
                    ) : null}
                  </span>
                  <button
                    className="studio-secondary"
                    disabled={busy || (numbers?.owned.length ?? 0) >= (numbers?.max ?? 3)}
                    onClick={() => changeNumbers("claim", n.number)}
                  >
                    Claim
                  </button>
                </li>
              ))}
            </ul>
          )}
          {numbers && numbers.owned.length > 0 && (
            <div className="mt-4 border-t border-border pt-3">
              <p className="text-xs font-medium text-muted-foreground">Your numbers</p>
              <ul className="mt-2 space-y-1">
                {numbers.owned.map((n) => (
                  <li key={n} className="flex flex-wrap items-center justify-between gap-3 text-sm">
                    <span>{n}</span>
                    {(numbers.releasable ?? []).includes(n) ? (
                      <button
                        className="text-xs text-danger disabled:opacity-40"
                        disabled={busy}
                        onClick={() => changeNumbers("release", n)}
                      >
                        Release
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">Assigned by your administrator</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        {number && (
          <div className="rounded-lg bg-muted/60 p-4 text-sm leading-6">
            <p className="font-medium">Forward your practice line to {number}</p>
            <p className="mt-2 text-muted-foreground">
              Callers keep dialling your usual number. We recommend forwarding only calls nobody
              answers, so your team still picks up first.
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>
                Calls nobody answers: dial <code className="break-all">**61*{number}#</code>
              </li>
              <li>
                All calls: dial <code className="break-all">**21*{number}#</code>
              </li>
              <li>
                To stop forwarding: <code>##61#</code> or <code>##21#</code>
              </li>
            </ul>
            <p className="mt-2 text-muted-foreground">
              These codes work on most mobile and many landline connections; carriers differ. A
              phone system such as a FRITZ!Box or a cloud PBX has a call-forwarding setting —
              enter this number there. Then call your practice number once to test it.
            </p>
          </div>
        )}
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
        {(() => {
          // Say why the button is disabled, rather than leaving it grey and silent.
          const missing = [
            !revision && "a published revision (Publish first)",
            !connection && "the practice system account above",
            !number && "a phone number",
          ].filter(Boolean);
          return missing.length ? (
            <p className="text-sm text-muted-foreground">
              To activate, choose {missing.join(", ")}.
            </p>
          ) : null;
        })()}
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
