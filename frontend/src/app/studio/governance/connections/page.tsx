"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type {
  CalendarSystem,
  ConnectionSummary,
  DoctolibLoginStatus,
} from "@/types";
import { Field, Section } from "@/features/agents/Fields";

/** The practice's own calendar labels, mapped to the system's ids. MA1–MA3 is the usual start. */
const DEFAULT_MAPPING = [
  { name: "MA1", id: "" },
  { name: "MA2", id: "" },
  { name: "MA3", id: "" },
];

/** What an agent on this system does, said plainly, so a read-only system never looks like one
 *  that books. */
function capabilityNote(system: CalendarSystem): string {
  if (system.capabilities.includes("booking")) return "";
  return ` ${system.label} cannot take bookings by phone yet: your agent tells callers which times are free and passes their booking request to your team.`;
}

export default function ConnectionsPage() {
  const [rows, setRows] = useState<ConnectionSummary[]>([]);
  const [systems, setSystems] = useState<CalendarSystem[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [systemKey, setSystemKey] = useState("");
  const [label, setLabel] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mapping, setMapping] = useState(DEFAULT_MAPPING);
  const [job, setJob] = useState<DoctolibLoginStatus | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const system = systems.find((s) => s.key === systemKey);

  const load = () =>
    Promise.all([api.listConnections(), api.listCalendarSystems()])
      .then(([connections, available]) => {
        setRows(connections);
        setSystems(available);
        setSystemKey((current) => current || available[0]?.key || "");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  useEffect(() => {
    void load();
  }, []);

  function finish(message: string) {
    setPassword("");
    setCode("");
    setJob(null);
    setOpen(false);
    setNotice(message);
    void load();
  }

  // A login that may ask for an emailed code runs on the server; follow it until it settles.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const timer = setTimeout(() => {
      api
        .pollDoctolibLogin(job.job_id)
        .then((next) => {
          if (next.status === "done" && next.connection_id) {
            finish(
              "Account connected. Select it in your agent’s Phone & handoff section and run a calendar check before activation.",
            );
          } else if (next.status === "failed") {
            setJob(null);
            setError(next.error || "The login did not complete. Check the details and try again.");
          } else {
            setJob(next);
          }
        })
        .catch((e) => {
          setJob(null);
          setError(e.message);
        });
    }, 2000);
    return () => clearTimeout(timer);
  }, [job]);

  function mappingFor(target: CalendarSystem): Record<string, string | number> {
    const named = mapping.map((m) => ({ name: m.name.trim(), id: m.id.trim() }));
    if (
      named.some((m) => !m.name || !m.id) ||
      new Set(named.map((m) => m.name)).size !== named.length
    )
      throw new Error(
        `Every calendar needs a unique label and a ${target.mapping.label}.`,
      );
    if (target.mapping.numeric && named.some((m) => !/^\d+$/.test(m.id) || Number(m.id) < 1))
      throw new Error(`Each ${target.mapping.label} is a positive number.`);
    return Object.fromEntries(
      named.map((m) => [m.name, target.mapping.numeric ? Number(m.id) : m.id]),
    );
  }

  async function connect() {
    if (!system) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const mapped = mappingFor(system);
      if (system.connect === "password_and_code") {
        setJob(
          await api.startDoctolibLogin({
            username,
            password,
            label: label.trim(),
            agendas: Object.fromEntries(
              Object.entries(mapped).map(([k, v]) => [k, String(v)]),
            ),
          }),
        );
      } else {
        await api.createConnection({
          adapter: system.key,
          type: "calendar",
          credentials: { username, password },
          config: { label: label.trim(), [system.mapping.config_key]: mapped },
        });
        finish(
          "Account saved. Select it in your agent’s Phone & handoff section and run a calendar check before activation.",
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function sendCode() {
    if (!job) return;
    setBusy(true);
    setError("");
    try {
      setJob(await api.submitDoctolibCode(job.job_id, code.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const names = systems.map((s) => s.label).join(" or ");
  const waiting = job !== null && job.status !== "awaiting_code";

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-9 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">
            Practice accounts
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            The real systems your agent reads appointments from and books
            into. An agent can only act on an account you connected here.
          </p>
        </div>
        <button className="studio-primary" onClick={() => setOpen(!open)}>
          {open ? "Close setup" : "+ Connect a practice system"}
        </button>
      </div>
      {error && (
        <p
          className="mt-5 rounded-lg bg-danger/10 p-4 text-sm text-danger"
          role="alert"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mt-5 rounded-lg bg-success/10 p-4 text-sm text-success"
          role="status"
        >
          {notice}
        </p>
      )}
      {open && system && (
        <form
          className="mt-7"
          onSubmit={(e) => {
            e.preventDefault();
            void (job?.status === "awaiting_code" ? sendCode() : connect());
          }}
        >
          <Section
            title={`Connect your ${system.label} account`}
            description={`Credentials are encrypted on the server. Reconnecting creates a new account connection; live agents keep their current assignment until you switch it.${capabilityNote(system)}`}
          >
            <Field label="Practice system">
              <div className="flex flex-wrap gap-2" role="radiogroup">
                {systems.map((s) => (
                  <button
                    type="button"
                    role="radio"
                    aria-checked={s.key === system.key}
                    key={s.key}
                    disabled={job !== null}
                    className={s.key === system.key ? "studio-primary" : "studio-secondary"}
                    onClick={() => setSystemKey(s.key)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Connection name">
              <input
                required
                className="studio-input"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Main practice calendar"
              />
            </Field>
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label={`${system.label} email or username`}>
                <input
                  required
                  autoComplete="username"
                  className="studio-input"
                  value={username}
                  disabled={job !== null}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </Field>
              <Field label="Password">
                <input
                  required
                  type="password"
                  autoComplete="current-password"
                  className="studio-input"
                  value={password}
                  disabled={job !== null}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </div>
            <div className="border-t border-border pt-5">
              <h3 className="text-sm font-semibold">Map your calendars</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Give each calendar your agent books into its {system.mapping.label}.
                Labels must match the agent’s bookable calendars. These are
                ids from {system.label}, not names or list positions.
              </p>
              <div className="mt-4 space-y-3">
                {mapping.map((r, i) => (
                  <div
                    className="grid grid-cols-[1fr_1fr_auto] items-end gap-3"
                    key={i}
                  >
                    <Field label="Calendar label">
                      <input
                        required
                        className="studio-input"
                        value={r.name}
                        disabled={job !== null}
                        onChange={(e) =>
                          setMapping(
                            mapping.map((v, j) =>
                              j === i ? { ...v, name: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </Field>
                    <Field label={system.mapping.label}>
                      <input
                        required
                        inputMode={system.mapping.numeric ? "numeric" : "text"}
                        className="studio-input"
                        value={r.id}
                        disabled={job !== null}
                        onChange={(e) =>
                          setMapping(
                            mapping.map((v, j) =>
                              j === i ? { ...v, id: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </Field>
                    <button
                      type="button"
                      aria-label={`Remove calendar ${r.name}`}
                      disabled={mapping.length === 1 || job !== null}
                      className="py-3 text-danger disabled:opacity-30"
                      onClick={() => setMapping(mapping.filter((_, j) => j !== i))}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="mt-4 text-sm text-primary"
                disabled={job !== null}
                onClick={() => setMapping([...mapping, { name: "", id: "" }])}
              >
                + Add calendar
              </button>
            </div>
            {job?.status === "awaiting_code" && (
              <Field
                label="Code from your email"
                hint={`${system.label} sent a one-time code because this is a new device. You only enter it once.`}
              >
                <input
                  required
                  autoFocus
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  className="studio-input"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </Field>
            )}
            <button disabled={busy || waiting} type="submit" className="studio-primary">
              {job?.status === "awaiting_code"
                ? busy
                  ? "Confirming…"
                  : "Confirm code"
                : waiting
                  ? `Signing in to ${system.label}…`
                  : busy
                    ? "Connecting…"
                    : "Save connection"}
            </button>
          </Section>
        </form>
      )}
      <div className="mt-8 space-y-4">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading connections…</p>
        ) : rows.length === 0 ? (
          <Section
            title="No accounts connected yet"
            description={`Connect ${names || "your practice system"} to work with real appointments. You can rehearse your agent before connecting an account.`}
          >
            <Link href="/studio" className="text-sm text-primary">
              Go to your agents →
            </Link>
          </Section>
        ) : (
          rows.map((c) => {
            const owner = systems.find((s) => s.key === c.adapter);
            const mapped = c.mapping && Object.keys(c.mapping).length ? c.mapping : c.rooms || {};
            return (
              <Section
                key={c.id}
                title={c.label || owner?.label || c.adapter}
                description={`${owner?.label ?? c.adapter} · saved ${new Date(c.created_at).toLocaleDateString()}`}
              >
                <div className="flex flex-wrap gap-2">
                  {Object.entries(mapped).map(([name, id]) => (
                    <span className="chip" key={name}>
                      {name} → {id}
                    </span>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  {!owner
                    ? "Available for calendar imports."
                    : Object.keys(mapped).length
                      ? `Mapping saved. Check live access from your agent before activation.${capabilityNote(owner)}`
                      : "Calendar mapping missing. Reconnect with calendar IDs to use this account for voice."}
                </p>
                {owner && (
                  <button
                    className="text-sm text-primary"
                    onClick={() => {
                      setSystemKey(owner.key);
                      setLabel(c.label || owner.label);
                      setUsername("");
                      setPassword("");
                      setJob(null);
                      if (Object.keys(mapped).length)
                        setMapping(
                          Object.entries(mapped).map(([name, id]) => ({
                            name,
                            id: String(id),
                          })),
                        );
                      setOpen(true);
                      window.scrollTo({ top: 0, behavior: "smooth" });
                    }}
                  >
                    Reconnect account →
                  </button>
                )}
              </Section>
            );
          })
        )}
      </div>
    </main>
  );
}
