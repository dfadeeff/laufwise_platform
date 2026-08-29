"use client";

import { useCallback, useEffect, useState } from "react";

import { StudioHeader } from "@/components/studio/StudioHeader";
import { Notice, SectionTitle } from "@/components/studio/ui";
import { api } from "@/lib/api";
import type {
  ConversationDetail,
  ConversationEvent,
  ConversationOutcome,
  ConversationSummary,
} from "@/types";

// Saved calls — the conversational tier's timeline, read back.
//
// The point of this screen is to separate what the agent SAID from what it DID. A transcript alone
// cannot tell you whether a call worked: an agent that says "you're booked" while the engine
// blocked the write reads perfectly. So every tool call is shown with the result it actually got,
// and the outcome chip reports the engine's ruling, never the agent's wording.

const OUTCOME: Record<string, { label: string; chip: string; dot: string }> = {
  ok: {
    label: "booked",
    chip: "border-success/20 bg-success/10 text-success",
    dot: "bg-success",
  },
  blocked: {
    label: "blocked",
    chip: "border-warning/20 bg-warning/10 text-warning",
    dot: "bg-warning",
  },
  rejected: {
    label: "rejected",
    chip: "border-danger/20 bg-danger/10 text-danger",
    dot: "bg-danger",
  },
  state_unavailable: {
    label: "state unavailable",
    chip: "border-danger/20 bg-danger/10 text-danger",
    dot: "bg-danger",
  },
};

// A call that never reached a governed step is not a failure — nobody asked it to book anything.
const NOT_ATTEMPTED = {
  label: "no booking attempt",
  chip: "border-border bg-muted text-muted-foreground",
  dot: "bg-border",
};

const outcomeOf = (outcome: ConversationOutcome) =>
  (outcome && OUTCOME[outcome]) || NOT_ATTEMPTED;

function OutcomeChip({ outcome }: { outcome: ConversationOutcome }) {
  const o = outcomeOf(outcome);
  return (
    <span className={`shrink-0 rounded-md border px-2 py-0.5 font-mono text-[11px] ${o.chip}`}>
      {o.label}
    </span>
  );
}

/** "4 min ago" scans far faster than a timestamp when you are triaging a list. */
function ago(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [604800, "day"],
  ];
  const format = new Intl.RelativeTimeFormat([], { numeric: "auto" });
  let previous = 1;
  for (const [limit, unit] of steps) {
    if (seconds < limit) return format.format(-Math.round(seconds / previous), unit);
    previous = limit;
  }
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

/** The filters worth having on this screen: the question it exists to answer is "what worked?". */
const FILTERS = [
  { key: "all", label: "All calls" },
  { key: "ok", label: "Booked" },
  { key: "unresolved", label: "Not booked" },
  { key: "none", label: "No attempt" },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

function matches(call: ConversationSummary, filter: FilterKey): boolean {
  if (filter === "all") return true;
  if (filter === "none") return call.outcome === null;
  if (filter === "ok") return call.outcome === "ok";
  return call.outcome !== null && call.outcome !== "ok";
}

/** One call as a card: status first, then what it was about, then how it went. */
function CallCard({
  call,
  selected,
  onSelect,
}: {
  call: ConversationSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const language = String(call.metadata?.language ?? "");
  const footer = [
    ago(call.started_at),
    `${call.turns} turn${call.turns === 1 ? "" : "s"}`,
    call.tool_calls > 0 ? `${call.tool_calls} tool call${call.tool_calls === 1 ? "" : "s"}` : null,
    language ? language.toUpperCase() : null,
  ].filter(Boolean) as string[];

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      className={`w-full rounded-xl border bg-background p-4 text-left transition-colors hover:border-primary/40 ${
        selected ? "border-primary/50 ring-1 ring-primary/20" : "border-border"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="flex items-center gap-2 pt-0.5">
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${outcomeOf(call.outcome).dot}`}
            aria-hidden
          />
          <span className="font-mono text-[11px] text-muted-foreground">
            {call.channel} · {call.status}
          </span>
        </span>
        <OutcomeChip outcome={call.outcome} />
      </div>
      {/* The caller's opening line is the closest a call has to a subject. */}
      <p className="mt-2 line-clamp-2 text-sm text-ink">
        {call.opening ?? <span className="text-muted-foreground">No caller speech recorded</span>}
      </p>
      <p className="mt-2 font-mono text-[11px] text-muted-foreground">{footer.join("  ·  ")}</p>
    </button>
  );
}

function Turn({ payload }: { payload: Record<string, unknown> }) {
  const agent = payload.role === "agent";
  return (
    <div className={agent ? "sm:pr-10" : "sm:pl-10"}>
      <div className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
        {String(payload.role)}
      </div>
      <p
        className={`mt-1 rounded-lg px-3 py-2 text-sm text-foreground ${
          agent ? "bg-muted" : "bg-primary/10"
        }`}
      >
        {String(payload.text ?? "")}
      </p>
    </div>
  );
}

function ToolCall({ payload }: { payload: Record<string, unknown> }) {
  const result = (payload.result ?? {}) as Record<string, unknown>;
  const status = typeof result.status === "string" ? result.status : null;
  const args = (payload.arguments ?? {}) as Record<string, unknown>;
  const shown = Object.entries(args).filter(([, v]) => v !== null && v !== undefined && v !== "");
  // Dashed border, aligned under neither speaker: a tool call is the agent acting, not talking.
  return (
    <div className="rounded-lg border border-dashed border-border bg-background px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] font-medium text-ink">{String(payload.tool)}</span>
        {status && <OutcomeChip outcome={status as ConversationOutcome} />}
        {typeof payload.run_id === "string" && (
          <span className="font-mono text-[10px] text-muted-foreground">
            run {payload.run_id.slice(0, 8)}
          </span>
        )}
      </div>
      {shown.length > 0 && (
        <div className="mt-1.5 font-mono text-[11px] text-muted-foreground">
          {shown.map(([k, v]) => `${k}=${String(v)}`).join("  ")}
        </div>
      )}
      {/* The result is the whole point: it is how you tell an agent that booked from one that
          only said it did. Rendered verbatim, never summarised — but capped in height, because a
          fifteen-line payload in the middle of a transcript buries the conversation around it. */}
      <pre className="mt-1.5 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/60 p-2 font-mono text-[11px] text-foreground">
        {JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

function Timeline({ events }: { events: ConversationEvent[] }) {
  if (events.length === 0) {
    return <p className="mt-6 text-sm text-muted-foreground">This call recorded no events.</p>;
  }
  return (
    <div className="mt-5 space-y-4">
      {events.map((event) => (
        <div key={event.seq}>
          {event.kind === "turn" ? (
            <Turn payload={event.payload} />
          ) : event.kind === "tool_call" ? (
            <ToolCall payload={event.payload} />
          ) : (
            <div className="font-mono text-[11px] text-muted-foreground">{event.kind}</div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function CallsPage() {
  const [calls, setCalls] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("all");

  const load = useCallback(async () => {
    setError(null);
    try {
      const rows = await api.listConversations();
      setCalls(rows);
      if (rows.length > 0) setSelected(await api.getConversation(rows[0].conversation_id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = calls.filter((call) => matches(call, filter));

  const open = async (id: string) => {
    setError(null);
    try {
      setSelected(await api.getConversation(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <StudioHeader active="calls" />
      <main className="mx-auto max-w-6xl px-5 py-8 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">Calls</h1>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Every conversation the voice agent held, with what it said and what it actually did.
              The outcome is the engine&rsquo;s ruling on the governed write, not the agent&rsquo;s
              wording.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-md border border-border px-3 py-2 text-sm text-ink hover:bg-muted"
          >
            Refresh
          </button>
        </div>

        {error && (
          <div className="mt-6">
            <Notice tone="error">{error}</Notice>
          </div>
        )}

        {!loading && calls.length === 0 && !error && (
          <div className="mt-6">
            <Notice tone="info">
              No calls yet. Start one on the Voice test tab and it will appear here when it ends.
            </Notice>
          </div>
        )}

        {calls.length > 0 && (
          <div className="mt-6 flex flex-wrap items-center gap-2">
            {FILTERS.map((f) => {
              const count = calls.filter((c) => matches(c, f.key)).length;
              const active = filter === f.key;
              return (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? "border-primary/40 bg-primary/10 text-ink"
                      : "border-border text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {f.label}
                  <span className="ml-2 font-mono text-[11px] text-muted-foreground">{count}</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
          <aside>
            <SectionTitle>{loading ? "Loading" : `${shown.length} shown`}</SectionTitle>
            <div className="mt-3 max-h-[70vh] space-y-2.5 overflow-y-auto pr-1">
              {shown.map((call) => (
                <CallCard
                  key={call.conversation_id}
                  call={call}
                  selected={selected?.conversation_id === call.conversation_id}
                  onSelect={() => void open(call.conversation_id)}
                />
              ))}
              {!loading && shown.length === 0 && calls.length > 0 && (
                <p className="py-6 text-sm text-muted-foreground">No calls match this filter.</p>
              )}
            </div>
          </aside>

          <section className="min-h-[420px] rounded-xl border border-border bg-surface p-5">
            {selected ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SectionTitle>Timeline</SectionTitle>
                  <OutcomeChip outcome={selected.outcome} />
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 font-mono text-[11px] text-muted-foreground sm:grid-cols-4">
                  <div>
                    <dt>Started</dt>
                    <dd className="text-ink">{new Date(selected.started_at).toLocaleString()}</dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd className="text-ink">{selected.status}</dd>
                  </div>
                  <div>
                    <dt>Channel</dt>
                    <dd className="text-ink">{selected.channel}</dd>
                  </div>
                  <div>
                    {/* Which agent held this call. A transcript with no version is an anecdote. */}
                    <dt>Instance</dt>
                    <dd className="truncate text-ink">{selected.instance_id.slice(0, 8)}</dd>
                  </div>
                </dl>
                <Timeline events={selected.events} />
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                {loading ? "Loading calls…" : "Select a call to read it."}
              </p>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
