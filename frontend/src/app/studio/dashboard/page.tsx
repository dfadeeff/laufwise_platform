"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConversationSummary } from "@/types";
import type { StudioAgent } from "@/features/agents/types";

// How the practice is doing, counted from the same records Run history shows. Every number here
// is derived from a real endpoint — nothing is estimated, and a tier that has no data says so
// instead of showing a confident zero.

type Run = Awaited<ReturnType<typeof api.listRuns>>[number];
type Followup = Awaited<ReturnType<typeof api.listFollowups>>[number];

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const since = (iso: string) => Date.now() - new Date(iso).getTime() < WEEK_MS;

function Tile({
  label,
  value,
  detail,
  href,
}: {
  label: string;
  value: string;
  detail: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="studio-section transition hover:border-primary/40 hover:shadow-sm"
    >
      <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
        {label}
      </p>
      <p className="mt-3 text-3xl font-semibold tracking-tight text-ink">
        {value}
      </p>
      <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
    </Link>
  );
}

export default function DashboardPage() {
  const [agents, setAgents] = useState<StudioAgent[] | null>(null);
  const [calls, setCalls] = useState<ConversationSummary[] | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [followups, setFollowups] = useState<Followup[] | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    Promise.all([
      api.listAgents(),
      api.listConversations(),
      api.listRuns(),
      api.listFollowups(),
    ])
      .then(([a, c, r, f]) => {
        setAgents(a);
        setCalls(c);
        setRuns(r);
        setFollowups(f);
      })
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);
  const weekCalls = (calls ?? []).filter((c) => since(c.started_at));
  const booked = weekCalls.filter((c) => c.outcome === "ok").length;
  const blocked = weekCalls.filter(
    (c) => c.outcome && c.outcome !== "ok",
  ).length;
  const weekRuns = (runs ?? []).filter((r) => since(r.started_at));
  // A run's status is the engine's overall ruling: ok, blocked, rejected or state_unavailable.
  const stoppedRuns = weekRuns.filter((r) => r.status !== "ok").length;
  const open = (followups ?? []).filter((t) => t.status !== "completed");
  const recent = [
    ...(calls ?? []).map((c) => ({
      id: c.conversation_id,
      at: c.started_at,
      title: `Call · ${c.channel}`,
      detail: c.outcome ? c.outcome.replaceAll("_", " ") : "no booking attempt",
      href: `/studio/history?call=${c.conversation_id}`,
    })),
    ...(runs ?? []).map((r) => ({
      id: r.run_id,
      at: r.started_at,
      title: `Run · ${r.runbook.replaceAll("_", " ")}`,
      detail: r.status,
      href: "/studio/history?tab=runs",
    })),
  ]
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 6);
  const ready = agents && calls && runs && followups;
  return (
    <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-ink">
        Dashboard
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The last seven days, counted from your saved calls and runs.
      </p>
      {error && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
        >
          {error}
          <button className="ml-3 underline" onClick={load}>
            Retry
          </button>
        </div>
      )}
      {!ready && !error && (
        <p role="status" className="mt-12 text-sm text-muted-foreground">
          Counting…
        </p>
      )}
      {ready && (
        <>
          <div className="mt-8 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
            <Tile
              label="Agents"
              value={`${agents.filter((a) => a.channel?.active).length}/${agents.length}`}
              detail={
                agents.length
                  ? "answering calls / built"
                  : "Nothing built yet — start with a voice agent."
              }
              href="/studio"
            />
            <Tile
              label="Calls this week"
              value={String(weekCalls.length)}
              detail={
                weekCalls.length
                  ? `${booked} booked · ${blocked} blocked or rejected`
                  : "No calls recorded in the last seven days."
              }
              href="/studio/history"
            />
            <Tile
              label="Workflow runs"
              value={String(weekRuns.length)}
              detail={
                weekRuns.length
                  ? `${weekRuns.length - stoppedRuns} verified · ${stoppedRuns} stopped by the engine`
                  : "No workflow runs in the last seven days."
              }
              href="/studio/history?tab=runs"
            />
            <Tile
              label="Needs attention"
              value={String(open.length)}
              detail={
                open.length
                  ? "callbacks and summaries waiting for a person"
                  : "Nothing is waiting on your team."
              }
              href="/studio/history"
            />
          </div>

          <section className="studio-section mt-8">
            <h2 className="text-lg font-semibold tracking-tight">
              Latest activity
            </h2>
            {recent.length === 0 ? (
              <p className="mt-3 text-sm text-muted-foreground">
                Nothing has run yet. Rehearse a call from your agent&rsquo;s
                Tests section and it will appear here.
              </p>
            ) : (
              <ul className="mt-4">
                {recent.map((item) => (
                  <li
                    key={item.id}
                    className="border-b border-border last:border-0"
                  >
                    <Link
                      href={item.href}
                      className="flex flex-wrap items-center justify-between gap-3 py-3.5 hover:text-primary"
                    >
                      <span className="text-sm font-medium">{item.title}</span>
                      <span className="text-xs text-muted-foreground">
                        {item.detail} ·{" "}
                        {new Date(item.at).toLocaleString([], {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <p className="mt-6 text-xs leading-5 text-muted-foreground">
            Outcomes come from the engine&rsquo;s ruling on each governed write,
            not from what an agent said. Audio quality and caller satisfaction
            are not measured here.
          </p>
        </>
      )}
    </main>
  );
}
