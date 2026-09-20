"use client";
import { Followups } from "@/components/studio/Followups";

import { useCallback, useEffect, useState } from "react";

import { Icon } from "@/components/studio/icons";
import { Notice } from "@/components/studio/ui";
import { api } from "@/lib/api";
import { STATUS } from "@/lib/status";
import type {
  ConversationDetail,
  ConversationEvent,
  ConversationOutcome,
  ConversationSummary,
  StepResult,
} from "@/types";

// Saved calls — the conversational tier's timeline, read back.
//
// The point of this screen is to separate what the agent SAID from what it DID. A transcript
// alone cannot tell you whether a call worked: an agent that says "you're booked" while the
// engine blocked the write reads perfectly. So every tool call is shown with the result it
// actually got, the outcome chip reports the engine's ruling rather than the agent's wording,
// and the rail on the right carries the governed run's own step ledger — the preconditions and
// postconditions the engine evaluated, pass or fail. That ledger is the answer to "how do you
// know it booked", and until now it was only reachable through the raw runs console.

const OUTCOME: Record<string, { label: string; chip: string; dot: string }> = {
  ok: {
    label: "booked",
    chip: "border-success/25 bg-success/10 text-success",
    dot: "bg-success",
  },
  blocked: {
    label: "blocked",
    chip: "border-warning/25 bg-warning/10 text-warning",
    dot: "bg-warning",
  },
  rejected: {
    label: "rejected",
    chip: "border-danger/25 bg-danger/10 text-danger",
    dot: "bg-danger",
  },
  state_unavailable: {
    label: "state unavailable",
    chip: "border-danger/25 bg-danger/10 text-danger",
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
    <span
      className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${o.chip}`}
    >
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
    if (seconds < limit)
      return format.format(-Math.round(seconds / previous), unit);
    previous = limit;
  }
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

/** Seconds since the call opened, as m:ss — the reading position in a conversation. */
function offset(at: string, start: string): string {
  const s = Math.max(
    0,
    Math.round((new Date(at).getTime() - new Date(start).getTime()) / 1000),
  );
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function duration(call: ConversationSummary): string | null {
  if (!call.ended_at) return null;
  return offset(call.ended_at, call.started_at);
}

const isRehearsal = (call: ConversationSummary) =>
  call.metadata?.mode === "rehearsal" || call.metadata?.calendar === "sandbox";

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

/** One call in the list: status first, then what it was about, then how it went. */
function CallCard({
  call,
  selected,
  onSelect,
}: {
  call: ConversationSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const length = duration(call);
  const footer = [
    isRehearsal(call) ? "Rehearsal" : "Live",
    ago(call.started_at),
    length,
    `${call.turns} turn${call.turns === 1 ? "" : "s"}`,
  ].filter(Boolean) as string[];

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      className={`w-full rounded-xl border bg-white p-3.5 text-left transition ${
        selected
          ? "border-primary/50 ring-1 ring-primary/20"
          : "border-border hover:border-primary/30"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="flex items-center gap-2 pt-0.5 text-[11px] text-muted-foreground">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${outcomeOf(call.outcome).dot}`}
            aria-hidden
          />
          {call.direction} · {call.channel}
        </span>
        <OutcomeChip outcome={call.outcome} />
      </div>
      {/* The caller's opening line is the closest a call has to a subject. */}
      <p className="mt-2 line-clamp-2 text-[13px] leading-6 text-ink">
        {call.opening ?? (
          <span className="text-muted-foreground">No caller speech recorded</span>
        )}
      </p>
      <p className="mt-2 text-[11px] text-faint">{footer.join("  ·  ")}</p>
    </button>
  );
}

function Turn({
  payload,
  at,
}: {
  payload: Record<string, unknown>;
  at: string | null;
}) {
  const agent = payload.role === "agent";
  const who = String(payload.role ?? "?");
  return (
    <div className="flex gap-3">
      <span
        aria-hidden
        className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg text-[10px] font-semibold uppercase ${
          agent ? "bg-accent text-primary" : "bg-muted text-muted-foreground"
        }`}
      >
        {who.slice(0, 2)}
      </span>
      <p className="min-w-0 flex-1 text-[13px] leading-6 text-ink">
        <span className="sr-only">{who}: </span>
        {String(payload.text ?? "")}
      </p>
      {at && (
        <span className="shrink-0 pt-1 text-[11px] tabular-nums text-faint">
          {at}
        </span>
      )}
    </div>
  );
}

function ToolCall({
  payload,
  at,
}: {
  payload: Record<string, unknown>;
  at: string | null;
}) {
  const result = (payload.result ?? {}) as Record<string, unknown>;
  const status = typeof result.status === "string" ? result.status : null;
  const args = (payload.arguments ?? {}) as Record<string, unknown>;
  const shown = Object.entries(args).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  // Dashed border, aligned under neither speaker: a tool call is the agent acting, not talking.
  return (
    <div className="ml-9 rounded-lg border border-dashed border-border bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] font-medium text-ink">
          {String(payload.tool)}
        </span>
        {status && <OutcomeChip outcome={status as ConversationOutcome} />}
        {typeof payload.run_id === "string" && (
          <span className="font-mono text-[10px] text-faint">
            run {payload.run_id.slice(0, 8)}
          </span>
        )}
        {at && (
          <span className="ml-auto text-[11px] tabular-nums text-faint">
            {at}
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
      <pre className="mt-1.5 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-md bg-white p-2 font-mono text-[11px] text-foreground ring-1 ring-border">
        {JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

/** The summary the practice was sent — and whether it actually went out.
 *
 * Stored on every call (spec §3.9 sends one without exception) but invisible until now: the
 * timeline rendered turns and tool calls and dropped everything else on the floor. "The practice
 * was never told" is exactly the fact you need to be able to see. */
function CallSummary({ payload }: { payload: Record<string, unknown> }) {
  const summary = (payload.summary ?? {}) as Record<string, unknown>;
  const delivery = (payload.delivery ?? {}) as Record<string, unknown>;
  const sent = delivery.sent === true;
  const recipients = Array.isArray(delivery.recipients)
    ? (delivery.recipients as string[])
    : [];
  return (
    <div className="ml-9 rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] font-medium text-ink">
          summary to the practice
        </span>
        <span className="rounded-md border border-border bg-white px-2 py-0.5 font-mono text-[11px] text-ink">
          {String(summary.outcome ?? "—")}
        </span>
        <span
          className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
            sent
              ? "border-success/20 bg-success/10 text-success"
              : "border-warning/20 bg-warning/10 text-warning"
          }`}
        >
          {sent
            ? `emailed ${recipients.length}`
            : `not emailed — ${String(delivery.reason ?? "?")}`}
        </span>
        {summary.staff_action_required === true && (
          <span className="rounded-md border border-warning/20 bg-warning/10 px-2 py-0.5 font-mono text-[11px] text-warning">
            staff action required
          </span>
        )}
      </div>
      <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-white p-2 font-mono text-[11px] text-foreground ring-1 ring-border">
        {JSON.stringify(summary, null, 2)}
      </pre>
    </div>
  );
}

/** Download this call as Markdown.
 *
 * A saved call is the audit record and reads like one. Keeping it as an EXAMPLE — for a review,
 * a message to the practice, or the seed of an eval scenario — needs a file, and JSONB is not a
 * file. The rendering keeps the distinction this screen is built on: what the agent said and
 * what it actually did stay separate. */
function SaveExample({ conversationId }: { conversationId: string }) {
  const [state, setState] = useState<"idle" | "saving" | "failed">("idle");
  const save = useCallback(async () => {
    setState("saving");
    try {
      const markdown = await api.exportConversation(conversationId);
      const url = URL.createObjectURL(
        new Blob([markdown], { type: "text/markdown" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = `call-${conversationId.slice(0, 8)}.md`;
      link.click();
      URL.revokeObjectURL(url);
      setState("idle");
    } catch {
      setState("failed");
    }
  }, [conversationId]);
  return (
    <button
      type="button"
      onClick={save}
      disabled={state === "saving"}
      className="studio-secondary"
    >
      {state === "saving"
        ? "Saving…"
        : state === "failed"
          ? "Failed — retry"
          : "Save example"}
    </button>
  );
}

function Timeline({
  events,
  start,
}: {
  events: ConversationEvent[];
  start: string;
}) {
  if (events.length === 0) {
    return (
      <p className="mt-6 text-sm text-muted-foreground">
        This call recorded no events.
      </p>
    );
  }
  return (
    <div className="mt-5 space-y-3.5">
      {events.map((event) => {
        const at = event.created_at ? offset(event.created_at, start) : null;
        return (
          <div key={event.seq}>
            {event.kind === "turn" ? (
              <Turn payload={event.payload} at={at} />
            ) : event.kind === "tool_call" ? (
              <ToolCall payload={event.payload} at={at} />
            ) : event.kind === "call_summary" ? (
              <CallSummary payload={event.payload} />
            ) : (
              <div className="ml-9 font-mono text-[11px] text-faint">
                {event.kind}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// --- the outcome rail ---------------------------------------------------------------------

/** A step of a governed run, as a check. `step_id` is an identifier the runbook author chose,
 *  so it reads better with its underscores knocked out than it does raw. */
const readable = (stepId: string) =>
  stepId.replaceAll(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase());

function Check({ step }: { step: StepResult }) {
  const s = STATUS[step.status];
  return (
    <li className="flex items-start gap-2.5 py-2">
      <span
        aria-hidden
        className={`mt-px text-[13px] leading-5 ${step.status === "ok" ? "text-success" : step.status === "rejected" ? "text-danger" : "text-warning"}`}
      >
        {s.glyph}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] leading-5 text-ink">
          {readable(step.step_id)}
        </span>
        {/* The engine's own words for why, never a paraphrase. */}
        {(step.reason || step.expr || step.blocked_tool) && (
          <span className="mt-0.5 block font-mono text-[11px] leading-5 text-muted-foreground">
            {step.reason ?? step.expr ?? `tool refused: ${step.blocked_tool}`}
          </span>
        )}
      </span>
      <span className="shrink-0 text-[11px] text-faint">{s.label}</span>
    </li>
  );
}

function OutcomeRail({
  call,
  checks,
  checksState,
}: {
  call: ConversationDetail;
  checks: StepResult[];
  checksState: "loading" | "ready" | "none";
}) {
  const passed = checks.filter((s) => s.status === "ok").length;
  const tools = call.events.filter((e) => e.kind === "tool_call");
  return (
    <aside aria-label="Call outcome" className="space-y-6">
      <div>
        <p className="studio-eyebrow">Engine ruling</p>
        <div className="mt-2.5 rounded-xl border border-border bg-white p-4">
          <OutcomeChip outcome={call.outcome} />
          <p className="mt-2.5 text-[13px] leading-6 text-muted-foreground">
            {call.outcome === "ok"
              ? "The engine re-read the calendar after the write and found the appointment. This is the postcondition passing, not the agent reporting success."
              : call.outcome === null
                ? "This call never attempted a governed write, so there was nothing for the engine to rule on."
                : "The engine refused the write. Whatever the agent said to the caller, nothing was recorded in the calendar."}
          </p>
        </div>
      </div>

      <div>
        <div className="flex items-baseline justify-between gap-2">
          <p className="studio-eyebrow">Booking checks</p>
          {checksState === "ready" && (
            <span className="text-[11px] text-faint">
              {passed} of {checks.length} passed
            </span>
          )}
        </div>
        {checksState === "loading" && (
          <p className="mt-2 text-[13px] text-muted-foreground">
            Reading the governed runs…
          </p>
        )}
        {checksState === "none" && (
          <p className="mt-2 text-[13px] leading-6 text-muted-foreground">
            No governed run is attached to this call. Nothing was written, so
            there is no step ledger to show.
          </p>
        )}
        {checksState === "ready" && (
          <ul className="mt-1.5 divide-y divide-border">
            {checks.map((step, i) => (
              <Check key={`${step.step_id}-${i}`} step={step} />
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="studio-eyebrow">Trace</p>
        {tools.length === 0 ? (
          <p className="mt-2 text-[13px] text-muted-foreground">
            The agent took no action on this call.
          </p>
        ) : (
          <ol className="mt-2 space-y-1.5">
            {tools.map((event) => {
              const result = (event.payload.result ?? {}) as Record<
                string,
                unknown
              >;
              return (
                <li key={event.seq} className="flex gap-2.5 text-[12px]">
                  <span className="shrink-0 tabular-nums text-faint">
                    {event.created_at
                      ? offset(event.created_at, call.started_at)
                      : "—"}
                  </span>
                  <span className="min-w-0 font-mono text-muted-foreground">
                    <span className="text-ink">{String(event.payload.tool)}</span>
                    {" → "}
                    {String(result.status ?? "no status")}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </aside>
  );
}

/** The calls half of Run history: what the agent said, next to what the engine let it do. */
export function CallsView() {
  const [calls, setCalls] = useState<ConversationSummary[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [checks, setChecks] = useState<StepResult[]>([]);
  const [checksState, setChecksState] = useState<"loading" | "ready" | "none">(
    "none",
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterKey>("all");

  // The step ledger for a call: every governed run its tool calls started, in order. Each run
  // is fetched on its own and a failure is dropped rather than raised — a run whose trace has
  // aged out must not take the transcript down with it.
  const loadChecks = useCallback(async (call: ConversationDetail) => {
    const runIds = [
      ...new Set(
        call.events
          .filter((e) => e.kind === "tool_call")
          .map((e) => e.payload.run_id)
          .filter((id): id is string => typeof id === "string"),
      ),
    ];
    if (runIds.length === 0) {
      setChecks([]);
      setChecksState("none");
      return;
    }
    setChecksState("loading");
    const runs = await Promise.all(
      runIds.map((id) => api.getRun(id).catch(() => null)),
    );
    const steps = runs.flatMap((run) => run?.steps ?? []);
    setChecks(steps);
    setChecksState(steps.length ? "ready" : "none");
  }, []);

  const show = useCallback(
    async (detail: ConversationDetail) => {
      setSelected(detail);
      void loadChecks(detail);
    },
    [loadChecks],
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      const rows = await api.listConversations();
      setCalls(rows);
      const requested = new URLSearchParams(window.location.search).get("call");
      if (requested) await show(await api.getConversation(requested));
      else if (rows.length > 0)
        await show(await api.getConversation(rows[0].conversation_id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = calls.filter((call) => matches(call, filter));

  const open = async (id: string) => {
    setError(null);
    try {
      await show(await api.getConversation(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const length = selected ? duration(selected) : null;

  return (
    <div>
      <Followups />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
          Every conversation the voice agent held, with what it said and what it
          actually did. The outcome is the engine&rsquo;s ruling on the governed
          write, not the agent&rsquo;s wording.
        </p>
        <button
          type="button"
          onClick={() => void load()}
          className="studio-secondary"
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
            No calls yet. Start one on the Tests tab of an agent and it will
            appear here when it ends.
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
                aria-pressed={active}
                className={`rounded-lg border px-3 py-1.5 text-[13px] transition ${
                  active
                    ? "border-primary/40 bg-accent/50 font-medium text-ink"
                    : "border-border bg-white text-muted-foreground hover:text-ink"
                }`}
              >
                {f.label}
                <span className="ml-2 text-[11px] tabular-nums text-faint">
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-5 grid items-start gap-5 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)] xl:grid-cols-[minmax(0,300px)_minmax(0,1fr)_minmax(0,290px)]">
        <div>
          <p className="studio-eyebrow">
            {loading ? "Loading" : `${shown.length} shown`}
          </p>
          <div className="mt-2.5 max-h-[72vh] space-y-2 overflow-y-auto pr-1">
            {shown.map((call) => (
              <CallCard
                key={call.conversation_id}
                call={call}
                selected={selected?.conversation_id === call.conversation_id}
                onSelect={() => void open(call.conversation_id)}
              />
            ))}
            {!loading && shown.length === 0 && calls.length > 0 && (
              <p className="py-6 text-sm text-muted-foreground">
                No calls match this filter.
              </p>
            )}
          </div>
        </div>

        <section className="min-h-[420px] rounded-xl border border-border bg-white p-5">
          {selected ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2.5">
                    <h2 className="text-[15px] font-semibold capitalize text-ink">
                      {selected.direction} call
                    </h2>
                    <OutcomeChip outcome={selected.outcome} />
                  </div>
                  <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[12px] text-muted-foreground">
                    <span>
                      {new Date(selected.started_at).toLocaleString()}
                    </span>
                    {length && (
                      <>
                        <span aria-hidden className="text-faint">
                          ·
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <Icon name="clock" size={12} width={2} />
                          {length}
                        </span>
                      </>
                    )}
                    <span aria-hidden className="text-faint">
                      ·
                    </span>
                    <span>{isRehearsal(selected) ? "Rehearsal" : "Live"}</span>
                    <span aria-hidden className="text-faint">
                      ·
                    </span>
                    {/* Which agent held this call. A transcript with no version is an anecdote. */}
                    <span className="font-mono text-[11px]">
                      {selected.instance_id.slice(0, 8)}
                    </span>
                  </p>
                </div>
                <SaveExample conversationId={selected.conversation_id} />
              </div>
              <div className="mt-5 border-t border-border pt-1">
                <Timeline
                  events={selected.events}
                  start={selected.started_at}
                />
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              {loading ? "Loading calls…" : "Select a call to read it."}
            </p>
          )}
        </section>

        {selected && (
          <div className="lg:col-span-2 xl:col-span-1">
            <OutcomeRail
              call={selected}
              checks={checks}
              checksState={checksState}
            />
          </div>
        )}
      </div>
    </div>
  );
}
