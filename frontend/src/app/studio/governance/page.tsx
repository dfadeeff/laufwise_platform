"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TemplateSummary } from "@/types";

// The contracts your agents run inside. A template is data, versioned and published through a
// gate; an agent cannot widen what its contract allows, which is the whole reason this page can
// be short. Approvals are listed honestly: the endpoint is a stub, so we say so.

function statusChip(status: string) {
  return status === "published"
    ? "bg-success/10 text-success"
    : "bg-muted text-muted-foreground";
}

export default function GovernancePage() {
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    api
      .listTemplates()
      .then(setTemplates)
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);
  // Newest version of each contract; the older ones stay immutable behind it.
  const latest = new Map<string, TemplateSummary>();
  for (const t of templates ?? [])
    if (!latest.has(t.name) || latest.get(t.name)!.version < t.version)
      latest.set(t.name, t);
  const contracts = [...latest.values()].sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  return (
    <main className="mx-auto max-w-5xl px-5 py-9 sm:px-8">
      <section className="studio-section">
        <h2 className="text-xl font-semibold tracking-tight">
          What the platform enforces
        </h2>
        <ul className="mt-4 space-y-3 text-sm leading-6 text-muted-foreground">
          <li>
            <strong className="text-ink">Nothing is edited or deleted.</strong>{" "}
            No agent has an update or delete capability, so a mistake adds a
            record instead of overwriting one.
          </li>
          <li>
            <strong className="text-ink">
              A booking counts when the calendar says so.
            </strong>{" "}
            After every write the engine re-reads your calendar. What the agent
            claims is never the evidence.
          </li>
          <li>
            <strong className="text-ink">
              Unclear state blocks the write.
            </strong>{" "}
            If your calendar cannot be read, the run stops rather than risking a
            double booking.
          </li>
          <li>
            <strong className="text-ink">Your data stays yours.</strong> Every
            agent, connection and run is scoped to this practice; an id from
            another practice is simply not found.
          </li>
        </ul>
      </section>

      <section className="mt-8">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-xl font-semibold tracking-tight">
            Governed contracts
          </h2>
          <Link
            href="/studio/author"
            className="text-sm text-muted-foreground hover:text-primary"
          >
            Advanced · Template authoring →
          </Link>
        </div>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          The step-by-step contracts agents execute. A published version is
          immutable — a change is a new version, never an edit in place.
        </p>
        {error && (
          <div
            role="alert"
            className="mt-5 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
          >
            {error}
            <button className="ml-3 underline" onClick={load}>
              Retry
            </button>
          </div>
        )}
        {!templates && !error && (
          <p role="status" className="mt-5 text-sm text-muted-foreground">
            Loading contracts…
          </p>
        )}
        {templates && contracts.length === 0 && (
          <p className="mt-5 text-sm text-muted-foreground">
            No contracts have been published yet.
          </p>
        )}
        {contracts.length > 0 && (
          <div className="studio-section mt-5 !p-0">
            <ul>
              {contracts.map((t) => (
                <li
                  key={t.name}
                  className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4 last:border-0 sm:px-7"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-ink">
                      {t.name
                        .replaceAll("_", " ")
                        .replace(/^./, (c) => c.toUpperCase())}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t.agent_class} · {t.step_count} steps · {t.risk} risk
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground">
                      v{t.version}
                    </span>
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-medium ${statusChip(t.status)}`}
                    >
                      {t.status}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="mt-8">
        <h2 className="text-xl font-semibold tracking-tight">Approvals</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
          A step that needs a human decision blocks the run today; there is no
          console here yet to release one. Until that is built, treat a blocked
          run in{" "}
          <Link
            href="/studio/history?tab=runs"
            className="text-primary underline"
          >
            Run history
          </Link>{" "}
          as the thing to act on.
        </p>
      </section>
    </main>
  );
}
