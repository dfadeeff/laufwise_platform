"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { WorkspaceService, WorkspaceServiceIn } from "@/types";
import { Field, Section } from "./Fields";

const LANGUAGES = [
  { code: "de", label: "German" },
  { code: "en", label: "English" },
  { code: "ru", label: "Russian" },
] as const;

const EMPTY: WorkspaceServiceIn = {
  name: "",
  external_id: "",
  duration_minutes: null,
  aliases: {},
  bookable: true,
  consultation: false,
};

const words = (text: string) =>
  text
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);

/** The workspace's services (ADR-0025): what its agents may book, each linked to the calendar
 *  system's own id. Shared by every agent of the workspace; an agent books what it pinned when it
 *  was last published. A service without that id is never booked by phone — callers get a
 *  callback for it. */
export function ServiceCatalog() {
  const [services, setServices] = useState<WorkspaceService[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // a service id, or "new"
  const [error, setError] = useState("");

  const load = () =>
    api
      .listServices()
      .then(setServices)
      .catch((e) => {
        setServices([]);
        setError(e instanceof Error ? e.message : String(e));
      });
  useEffect(() => {
    void load();
  }, []);

  const bookable = (services ?? []).filter((s) => s.phone_bookable).length;
  return (
    <Section
      title="Services"
      description="What callers can book. Your agent asks which one they need and never picks one for them. A service is booked by phone only once it is linked to your calendar system's own ID (in Thevea: the price-list position) — otherwise the caller gets a callback. Shared by all your agents; changes reach callers when you publish."
      meta={services && services.length ? `${bookable} of ${services.length} bookable by phone` : undefined}
    >
      {services === null ? (
        <p className="text-sm text-muted-foreground">Loading services…</p>
      ) : services.length === 0 && editing !== "new" ? (
        <p className="text-sm text-muted-foreground">
          No services yet. Without them your agent books one plain appointment. Add the treatments
          you offer to have callers book a specific one.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {services.map((s) =>
            editing === s.id ? (
              <li key={s.id} className="py-4">
                <ServiceForm
                  initial={s}
                  onCancel={() => setEditing(null)}
                  onSave={async (value) => {
                    await api.updateService(s.id, value);
                    setEditing(null);
                    await load();
                  }}
                  onDelete={async () => {
                    await api.deleteService(s.id);
                    setEditing(null);
                    await load();
                  }}
                />
              </li>
            ) : (
              <li key={s.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">
                    {s.name}
                    {s.consultation && (
                      <span className="ml-2 rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                        offered when unclear
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-xs">
                    {s.phone_bookable ? (
                      <span className="text-success">
                        Bookable by phone · ID {s.external_id}
                        {s.duration_minutes ? ` · ${s.duration_minutes} min` : ""}
                      </span>
                    ) : !s.bookable ? (
                      <span className="text-muted-foreground">Switched off — callers get a callback</span>
                    ) : (
                      <span className="text-warning">Not bookable yet — add the ID from your calendar system</span>
                    )}
                  </p>
                </div>
                <button type="button" className="text-sm text-primary" onClick={() => setEditing(s.id)}>
                  Edit
                </button>
              </li>
            ),
          )}
        </ul>
      )}
      {services && services.length > 0 && editing === null && <TryAPhrase />}
      {editing === "new" ? (
        <ServiceForm
          initial={EMPTY}
          onCancel={() => setEditing(null)}
          onSave={async (value) => {
            await api.addService(value);
            setEditing(null);
            await load();
          }}
        />
      ) : (
        <button type="button" className="studio-secondary" onClick={() => setEditing("new")}>
          Add a service
        </button>
      )}
      {error && (
        <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger [overflow-wrap:anywhere]" role="alert">
          {error}
        </p>
      )}
    </Section>
  );
}

/** Type what a caller might say and see what your agent would make of it — the same matching a
 *  call uses, on the services as they are now (a call uses them as of the last publish). */
function TryAPhrase() {
  const [said, setSaid] = useState("");
  const [matches, setMatches] = useState<WorkspaceService[] | null>(null);
  const [error, setError] = useState("");

  const outcome =
    matches === null
      ? null
      : matches.length === 0
        ? "No match — your agent asks what the treatment is for, then offers the consultation or a callback."
        : matches.length === 1
          ? `${matches[0].name} — your agent checks with the caller that this is what they mean.${matches[0].phone_bookable ? "" : " It is not bookable by phone, so they get a callback."}`
          : `Your agent asks which one: ${matches.map((m) => m.name).join(" or ")}.`;

  return (
    <form
      className="rounded-lg border border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        api
          .recogniseService(said)
          .then((r) => setMatches(r.matches))
          .catch((err) => setError(err instanceof Error ? err.message : String(err)));
      }}
    >
      <Field label="Try what a caller might say" hint="In any language you entered phrases for.">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            className="studio-input min-w-0 flex-1"
            required
            maxLength={300}
            placeholder="e.g. ich brauche eine Fußpflege"
            value={said}
            onChange={(e) => {
              setSaid(e.target.value);
              setMatches(null);
            }}
          />
          <button className="studio-secondary shrink-0">Try</button>
        </div>
      </Field>
      {outcome && <p className="mt-2 text-sm text-ink" role="status">{outcome}</p>}
      {error && (
        <p className="mt-2 rounded-lg bg-danger/10 p-3 text-sm text-danger [overflow-wrap:anywhere]" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function ServiceForm({
  initial,
  onSave,
  onCancel,
  onDelete,
}: {
  initial: WorkspaceServiceIn;
  onSave: (value: WorkspaceServiceIn) => Promise<void>;
  onCancel: () => void;
  onDelete?: () => Promise<void>;
}) {
  const [value, setValue] = useState(initial);
  const [aliases, setAliases] = useState<Record<string, string>>(
    Object.fromEntries(LANGUAGES.map((l) => [l.code, (initial.aliases[l.code] ?? []).join(", ")])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<WorkspaceServiceIn>) => setValue((v) => ({ ...v, ...patch }));

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void run(() =>
          onSave({
            ...value,
            aliases: Object.fromEntries(LANGUAGES.map((l) => [l.code, words(aliases[l.code] ?? "")])),
          }),
        );
      }}
    >
      <Field label="Service name" hint="As your practice names it, e.g. Podologische Komplexbehandlung Groß.">
        <input className="studio-input" required maxLength={160} value={value.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-[1fr_10rem]">
        <Field label="ID in your calendar system" hint="In Thevea: the position in the private price list. Empty: callers get a callback for it.">
          <input className="studio-input" maxLength={80} value={value.external_id} onChange={(e) => set({ external_id: e.target.value.trim() })} />
        </Field>
        <Field label="Duration (minutes)" hint="As verified by your practice.">
          <input
            className="studio-input"
            type="number"
            min={5}
            max={480}
            value={value.duration_minutes ?? ""}
            onChange={(e) => set({ duration_minutes: e.target.value ? Number(e.target.value) : null })}
          />
        </Field>
      </div>
      {LANGUAGES.map((l) => (
        <Field
          key={l.code}
          label={`How callers say it (${l.label})`}
          hint="Separate with commas. Put the same phrase on several services and your agent asks which one the caller means — e.g. “Fußpflege” on both the small and the full treatment."
        >
          <input
            className="studio-input"
            value={aliases[l.code] ?? ""}
            onChange={(e) => setAliases((a) => ({ ...a, [l.code]: e.target.value }))}
          />
        </Field>
      ))}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={value.bookable} onChange={(e) => set({ bookable: e.target.checked })} />
        Bookable by phone
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={value.consultation} onChange={(e) => set({ consultation: e.target.checked })} />
        Offer this when a caller cannot say what they need (e.g. an initial consultation)
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button className="studio-primary" disabled={busy}>{busy ? "Saving…" : "Save service"}</button>
        <button type="button" className="studio-secondary" onClick={onCancel}>Cancel</button>
        {onDelete && (
          <button type="button" className="ml-auto text-sm text-danger" disabled={busy} onClick={() => void run(onDelete)}>
            Remove service
          </button>
        )}
      </div>
      {error && (
        <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger [overflow-wrap:anywhere]" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
