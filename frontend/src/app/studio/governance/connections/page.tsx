"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { ConnectionSummary } from "@/types";
import { Field, Section } from "@/features/agents/Fields";
export default function ConnectionsPage() {
  const [rows, setRows] = useState<ConnectionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rooms, setRooms] = useState([
    { name: "MA1", id: "" },
    { name: "MA2", id: "" },
    { name: "MA3", id: "" },
  ]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const load = () =>
    api
      .listConnections()
      .then(setRows)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  useEffect(() => {
    void load();
  }, []);
  async function connect() {
    setBusy(true);
    setError("");
    try {
      if (
        rooms.some(
          (r) => !r.name.trim() || !/^\d+$/.test(r.id) || Number(r.id) < 1,
        ) ||
        new Set(rooms.map((r) => r.name.trim())).size !== rooms.length
      )
        throw new Error(
          "Every calendar needs a unique label and a positive Thevea room ID.",
        );
      await api.createConnection({
        adapter: "thevea",
        type: "calendar",
        credentials: { username, password },
        config: {
          label: label.trim(),
          rooms: Object.fromEntries(
            rooms.map((r) => [r.name.trim(), Number(r.id)]),
          ),
        },
      });
      setPassword("");
      setOpen(false);
      setNotice(
        "Account saved. Select it in your agent’s Phone & handoff section and run a calendar check before activation.",
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="mx-auto max-w-5xl px-5 py-9 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">
            Practice accounts
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            The real systems a booking is written to and read back from. An
            agent can only act on an account you connected here.
          </p>
        </div>
        <button className="studio-primary" onClick={() => setOpen(!open)}>
          {open ? "Close setup" : "+ Connect Thevea"}
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
      {open && (
        <form
          className="mt-7"
          onSubmit={(e) => {
            e.preventDefault();
            void connect();
          }}
        >
          <Section
            title="Connect your Thevea account"
            description="Credentials are encrypted on the server. Reconnecting creates a new account connection; live agents keep their current assignment until you switch it."
          >
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
              <Field label="Thevea email or username">
                <input
                  required
                  autoComplete="username"
                  className="studio-input"
                  value={username}
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
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </div>
            <div className="border-t border-border pt-5">
              <h3 className="text-sm font-semibold">Map your calendars</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Use the actual Thevea room / employee IDs provided by your
                integration administrator. Labels must match the agent’s
                bookable calendars. These are not names or list positions.
              </p>
              <div className="mt-4 space-y-3">
                {rooms.map((r, i) => (
                  <div
                    className="grid grid-cols-[1fr_1fr_auto] items-end gap-3"
                    key={i}
                  >
                    <Field label="Calendar label">
                      <input
                        required
                        className="studio-input"
                        value={r.name}
                        onChange={(e) =>
                          setRooms(
                            rooms.map((v, j) =>
                              j === i ? { ...v, name: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </Field>
                    <Field label="Thevea ID">
                      <input
                        required
                        type="number"
                        min={1}
                        className="studio-input"
                        value={r.id}
                        onChange={(e) =>
                          setRooms(
                            rooms.map((v, j) =>
                              j === i ? { ...v, id: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </Field>
                    <button
                      type="button"
                      aria-label={`Remove calendar ${r.name}`}
                      disabled={rooms.length === 1}
                      className="py-3 text-danger disabled:opacity-30"
                      onClick={() => setRooms(rooms.filter((_, j) => j !== i))}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="mt-4 text-sm text-primary"
                onClick={() => setRooms([...rooms, { name: "", id: "" }])}
              >
                + Add calendar
              </button>
            </div>
            <button disabled={busy} type="submit" className="studio-primary">
              {busy ? "Connecting…" : "Save connection"}
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
            description="Connect Thevea to book real appointments. You can rehearse your agent before connecting an account."
          >
            <Link href="/studio" className="text-sm text-primary">
              Go to your agents →
            </Link>
          </Section>
        ) : (
          rows.map((c) => (
            <Section
              key={c.id}
              title={c.label || c.adapter}
              description={`${c.adapter} · saved ${new Date(c.created_at).toLocaleDateString()}`}
            >
              <div className="flex flex-wrap gap-2">
                {Object.entries(c.rooms || {}).map(([name, id]) => (
                  <span className="chip" key={name}>
                    {name} → {id}
                  </span>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {c.adapter === "thevea"
                  ? Object.keys(c.rooms || {}).length
                    ? "Mapping saved. Check live access from your agent before activation."
                    : "Room mapping missing. Reconnect with calendar IDs to use this account for voice."
                  : "Available for calendar imports."}
              </p>
              {c.adapter === "thevea" && (
                <button
                  className="text-sm text-primary"
                  onClick={() => {
                    setLabel(c.label || "Thevea");
                    setUsername("");
                    setPassword("");
                    if (Object.keys(c.rooms || {}).length)
                      setRooms(
                        Object.entries(c.rooms!).map(([name, id]) => ({
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
          ))
        )}
      </div>
    </main>
  );
}
