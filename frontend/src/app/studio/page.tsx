"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import type { AgentConfig, StudioAgent } from "@/features/agents/types";
import {
  bookedShare,
  callMetrics,
  draftChanges,
  formatDuration,
  instanceIds,
  type CallMetrics,
} from "@/features/agents/insights";
import type {
  ConversationSummary,
  InstanceSummary,
  TemplateSummary,
} from "@/types";

// Everything this practice has built, and how it is doing. The three tiers are real and
// different: a voice receptionist talks to a caller, a workflow runs a fixed procedure, and the
// task tier — a model planning inside the governed loop — is not built yet. We say so rather
// than showing a create button that leads nowhere.
//
// Every figure on this page is read back from saved calls; none of it is decoration. Where the
// evidence is missing the cell says so with a dash, because the practice is going to make
// staffing decisions from these numbers.

const LANGUAGES: [AgentConfig["locale"], string][] = [
  ["de", "Deutsch"],
  ["en", "English"],
  ["ru", "Русский"],
  ["ar", "العربية"],
];

type State = "live" | "paused" | "published" | "draft";

const STATE: Record<State, { label: string; chip: string; dot: string }> = {
  live: {
    label: "Live",
    chip: "border-success/25 bg-success/10 text-success",
    dot: "bg-success",
  },
  paused: {
    label: "Paused",
    chip: "border-warning/25 bg-warning/10 text-warning",
    dot: "bg-warning",
  },
  published: {
    label: "Published",
    chip: "border-border bg-muted text-muted-foreground",
    dot: "bg-faint",
  },
  draft: {
    label: "Draft",
    chip: "border-border bg-muted text-muted-foreground",
    dot: "bg-border",
  },
};

const stateOf = (agent: StudioAgent): State =>
  agent.channel?.active
    ? "live"
    : agent.channel
      ? "paused"
      : agent.published_instance_id
        ? "published"
        : "draft";

function StateChip({ state }: { state: State }) {
  const s = STATE[state];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${s.chip}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

/** A number over its label. The unit of information on this page and in the inspector. */
function Stat({
  value,
  label,
  hint,
}: {
  value: string;
  label: string;
  hint?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="studio-stat" title={hint}>
        {value}
      </p>
      <p className="mt-1.5 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

/** Practice-wide traffic for the last seven days. Rehearsals are excluded everywhere: a test
 *  against a synthetic calendar is not something the practice handled. */
function StatStrip({
  metrics,
  available,
}: {
  metrics: CallMetrics;
  available: boolean;
}) {
  const share = bookedShare(metrics);
  const refused =
    metrics.attempts === 0
      ? null
      : Math.round((metrics.refused / metrics.attempts) * 100);
  const cells: [string, string, string?][] = available
    ? [
        [String(metrics.calls), "Calls this week"],
        [
          share === null ? "—" : `${share}%`,
          "Booked in call",
          "Share of booking attempts the engine ruled ok",
        ],
        [
          refused === null ? "—" : `${refused}%`,
          "Refused by the engine",
          "Blocked, rejected, or source state unavailable",
        ],
        [formatDuration(metrics.medianSeconds), "Median call"],
      ]
    : [
        ["—", "Calls this week"],
        ["—", "Booked in call"],
        ["—", "Refused by the engine"],
        ["—", "Median call"],
      ];
  return (
    <div className="mt-7 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-4">
      {cells.map(([value, label, hint]) => (
        <div key={label} className="bg-white px-5 py-4">
          <Stat value={value} label={label} hint={hint} />
        </div>
      ))}
    </div>
  );
}

/** One live agent, at a glance: is it answering, how much did it handle, and is the draft
 *  ahead of what callers are hearing. */
function AgentCard({
  agent,
  metrics,
  pending,
}: {
  agent: StudioAgent;
  metrics: CallMetrics;
  pending: number;
}) {
  const share = bookedShare(metrics);
  return (
    <div className="studio-section flex flex-col p-5 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent text-primary">
            <Icon name="phone" size={18} />
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-[15px] font-semibold text-ink">
              {agent.config.name}
            </h3>
            <p className="mt-0.5 truncate text-[13px] text-muted-foreground">
              {agent.config.practice_name || "Practice setup needed"}
            </p>
          </div>
        </div>
        <StateChip state={stateOf(agent)} />
      </div>
      <div className="mt-5 flex gap-8">
        <Stat value={String(metrics.calls)} label="calls · 7 d" />
        <Stat
          value={share === null ? "—" : `${share}%`}
          label="booked"
          hint="Share of booking attempts the engine ruled ok"
        />
      </div>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3.5 text-xs">
        <span className="text-muted-foreground">
          {agent.channel?.phone_number ?? "No phone connected"}
          {pending > 0 && (
            <>
              <span aria-hidden className="mx-2 text-faint">
                ·
              </span>
              <span className="text-primary">
                {pending} change{pending === 1 ? "" : "s"} to review
              </span>
            </>
          )}
        </span>
        <Link
          href={`/studio/agents/${agent.id}/overview`}
          className="inline-flex items-center gap-1 font-medium no-underline"
        >
          Open studio
          <Icon name="arrowRight" size={13} width={2} />
        </Link>
      </div>
    </div>
  );
}

/** Name it before it exists — an agent called "Receptionist" in a list of five is no name. */
function NewVoiceAgent({
  busy,
  onCreate,
  onCancel,
}: {
  busy: boolean;
  onCreate: (seed: {
    name: string;
    practice_name: string;
    locale: AgentConfig["locale"];
  }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("Empfang");
  const [practice, setPractice] = useState("");
  const [locale, setLocale] = useState<AgentConfig["locale"]>("de");
  return (
    <form
      className="studio-section mt-6"
      onSubmit={(e) => {
        e.preventDefault();
        onCreate({
          name: name.trim(),
          practice_name: practice.trim(),
          locale,
        });
      }}
    >
      <h2 className="text-lg font-semibold tracking-tight text-ink">
        New voice agent
      </h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Two details to start with. Everything else — hours, treatments, your
        greeting — comes next, and nothing answers a real phone until you
        connect a number.
      </p>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="studio-label">Agent name</span>
          <input
            autoFocus
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="studio-input mt-1.5"
            placeholder="Empfang"
          />
        </label>
        <label className="block">
          <span className="studio-label">Practice name</span>
          <input
            maxLength={160}
            value={practice}
            onChange={(e) => setPractice(e.target.value)}
            className="studio-input mt-1.5"
            placeholder="Praxis Nord"
          />
        </label>
        <label className="block">
          <span className="studio-label">Language spoken to callers</span>
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as AgentConfig["locale"])}
            className="studio-input mt-1.5"
          >
            {LANGUAGES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <button type="submit" disabled={busy} className="studio-primary">
          {busy ? "Creating…" : "Create agent"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="studio-secondary"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

/** The empty state is the setup path — with nothing built yet, the index IS the onboarding. */
function FirstAgent({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: () => void;
}) {
  return (
    <section className="studio-section grid items-center gap-8 lg:grid-cols-2">
      <div>
        <div className="mb-6 grid h-14 w-14 place-items-center rounded-2xl bg-accent text-primary">
          <Icon name="phone" size={24} />
        </div>
        <h3 className="text-2xl font-semibold tracking-tight text-ink">
          Meet your new receptionist
        </h3>
        <p className="mt-3 max-w-md text-sm leading-7 text-muted-foreground">
          Answer common questions, help patients find a time, and send your team
          the details. Set it up at your own pace before connecting your phone.
        </p>
        <button
          onClick={onStart}
          disabled={busy}
          className="studio-primary mt-6"
        >
          Set up your first agent
          <Icon name="arrowRight" size={14} width={2} />
        </button>
      </div>
      <ol className="space-y-5 rounded-xl bg-surface p-6 ring-1 ring-border">
        {[
          [
            "01",
            "Tell us about your practice",
            "Hours, treatments and how you welcome callers.",
          ],
          [
            "02",
            "Connect your calendar",
            "Choose exactly where appointments should go.",
          ],
          [
            "03",
            "Try a conversation",
            "Rehearse safely, then publish when you are ready.",
          ],
        ].map(([n, t, d]) => (
          <li key={n} className="flex gap-4">
            <span className="pt-0.5 font-mono text-xs font-medium text-primary">
              {n}
            </span>
            <div>
              <h4 className="text-sm font-medium text-ink">{t}</h4>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{d}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

const FILTERS = [
  { key: "all", label: "All" },
  { key: "live", label: "Live" },
  { key: "draft", label: "Draft" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

const matchesFilter = (state: State, filter: FilterKey) =>
  filter === "all" ||
  (filter === "live" ? state === "live" : state !== "live");

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<StudioAgent[] | null>(null);
  const [calls, setCalls] = useState<ConversationSummary[] | null>(null);
  const [workflows, setWorkflows] = useState<TemplateSummary[]>([]);
  const [legacy, setLegacy] = useState<InstanceSummary[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [query, setQuery] = useState("");

  const load = () => {
    setError("");
    Promise.all([api.listAgents(), api.listTemplates(), api.listInstances()])
      .then(([rows, templates, instances]) => {
        setAgents(rows);
        // One card per workflow, at its newest published version.
        const latest = new Map<string, TemplateSummary>();
        for (const t of templates)
          if (
            t.agent_class === "workflow" &&
            t.status === "published" &&
            (!latest.has(t.name) || latest.get(t.name)!.version < t.version)
          )
            latest.set(t.name, t);
        setWorkflows([...latest.values()]);
        setLegacy(
          instances.filter(
            (i) => !i.agent_id && i.phone_number && i.status === "deployed",
          ),
        );
      })
      .catch((e) => setError(e.message));
    // Metrics are read separately and allowed to fail on their own: a slow conversations
    // query must not keep you from opening the agent you came here to edit. When it does
    // fail every figure renders as a dash rather than as a zero.
    api
      .listConversations()
      .then(setCalls)
      .catch(() => setCalls(null));
  };
  useEffect(load, []);

  const create = async (seed: {
    name: string;
    practice_name: string;
    locale: AgentConfig["locale"];
  }) => {
    setBusy(true);
    setError("");
    try {
      const agent = await api.createAgent(seed);
      router.push(`/studio/agents/${agent.id}/overview`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  // Each agent's calls, found by the instance that held them. Computed once per load, not
  // once per row — an agent with a long revision history is a set lookup, not a scan.
  const perAgent = useMemo(() => {
    const byAgent = new Map<
      string,
      { metrics: CallMetrics; pending: number }
    >();
    for (const agent of agents ?? []) {
      const ids = instanceIds(agent);
      const live =
        agent.history.find((r) => r.id === agent.published_instance_id)
          ?.config ?? null;
      byAgent.set(agent.id, {
        metrics: callMetrics(
          (calls ?? []).filter((c) => ids.has(c.instance_id)),
        ),
        pending: draftChanges(agent.config, live).length,
      });
    }
    return byAgent;
  }, [agents, calls]);

  const practiceMetrics = useMemo(
    () => callMetrics(calls ?? []),
    [calls],
  );

  const shown = (agents ?? []).filter((a) => {
    const q = query.trim().toLowerCase();
    return (
      matchesFilter(stateOf(a), filter) &&
      (!q ||
        a.config.name.toLowerCase().includes(q) ||
        a.config.practice_name.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q))
    );
  });
  const featured = (agents ?? []).filter(
    (a) => stateOf(a) === "live" || stateOf(a) === "paused",
  );

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Agents" }]} />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="studio-eyebrow text-primary">Your team, extended</p>
          <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">
            Agents
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            A helpful first conversation. A booking your practice can trust.
          </p>
        </div>
        {!creating && agents && agents.length > 0 && (
          <button
            onClick={() => setCreating(true)}
            disabled={busy}
            className="studio-primary"
          >
            <Icon name="plus" size={15} width={2.2} />
            New voice agent
          </button>
        )}
      </div>

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

      {creating && (
        <NewVoiceAgent
          busy={busy}
          onCreate={create}
          onCancel={() => setCreating(false)}
        />
      )}

      {!agents && !error && (
        <p role="status" className="mt-12 text-sm text-muted-foreground">
          Loading your agents…
        </p>
      )}

      {agents && agents.length === 0 && !creating && (
        <div className="mt-8">
          <FirstAgent busy={busy} onStart={() => setCreating(true)} />
        </div>
      )}

      {agents && agents.length > 0 && (
        <>
          <StatStrip metrics={practiceMetrics} available={calls !== null} />

          {featured.length > 0 && (
            <div className="mt-6 grid gap-5 lg:grid-cols-2">
              {featured.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  metrics={perAgent.get(agent.id)?.metrics ?? callMetrics([])}
                  pending={perAgent.get(agent.id)?.pending ?? 0}
                />
              ))}
            </div>
          )}

          <section className="mt-10">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-[15px] font-semibold text-ink">
                All agents
                <span className="ml-2 font-normal text-muted-foreground">
                  {agents.length}
                </span>
              </h2>
              <div className="flex gap-1 rounded-lg bg-muted p-0.5">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    onClick={() => setFilter(f.key)}
                    aria-pressed={filter === f.key}
                    className={`rounded-[7px] px-2.5 py-1 text-[13px] transition ${filter === f.key ? "bg-white font-medium text-ink shadow-sm" : "text-muted-foreground hover:text-ink"}`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <label className="relative ml-auto">
                <span className="sr-only">Search agents</span>
                <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint">
                  <Icon name="search" size={15} />
                </span>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search agents"
                  className="h-9 w-full rounded-lg border border-border bg-white pl-8 pr-3 text-[13px] text-ink outline-none transition placeholder:text-faint focus:border-primary focus:ring-2 focus:ring-primary/15 sm:w-56"
                />
              </label>
            </div>

            <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-white">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {[
                      "Agent",
                      "Status",
                      "Number",
                      "Calls 7 d",
                      "Booked",
                      "Last published",
                    ].map((h, i) => (
                      <th
                        key={h}
                        scope="col"
                        className={`studio-eyebrow px-4 py-2.5 font-semibold ${i > 2 && i < 5 ? "text-right" : ""}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((agent) => {
                    const m = perAgent.get(agent.id)?.metrics ?? callMetrics([]);
                    const share = bookedShare(m);
                    // Highest revision rather than history[0]: the endpoint happens to sort
                    // newest-first, but this column would silently lie if that ever changed.
                    const last = agent.history.reduce<
                      (typeof agent.history)[number] | null
                    >((a, b) => (a && a.revision >= b.revision ? a : b), null);
                    return (
                      <tr
                        key={agent.id}
                        className="border-b border-border last:border-0 hover:bg-surface"
                      >
                        <td className="px-4 py-3">
                          <Link
                            href={`/studio/agents/${agent.id}/overview`}
                            className="font-medium text-ink no-underline hover:text-primary"
                          >
                            {agent.config.name}
                          </Link>
                          <p className="mt-0.5 font-mono text-[11px] text-faint">
                            {agent.id.slice(0, 12)}
                          </p>
                        </td>
                        <td className="px-4 py-3">
                          <StateChip state={stateOf(agent)} />
                        </td>
                        <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">
                          {agent.channel?.phone_number ?? "Not assigned"}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink">
                          {calls === null ? "—" : m.calls || "—"}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-ink">
                          {share === null ? "—" : `${share}%`}
                        </td>
                        <td className="px-4 py-3 text-[13px] text-muted-foreground">
                          {last
                            ? `Revision ${last.revision} · ${new Date(last.created_at).toLocaleDateString()}`
                            : "Never published"}
                        </td>
                      </tr>
                    );
                  })}
                  {shown.length === 0 && (
                    <tr>
                      <td
                        colSpan={6}
                        className="px-4 py-8 text-center text-sm text-muted-foreground"
                      >
                        No agents match this filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {agents && (
        <section className="mt-10">
          <h2 className="text-[15px] font-semibold text-ink">
            Workflows &amp; procedures
          </h2>
          <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
            A fixed sequence of steps, triggered rather than spoken to —
            calendar imports and the like. No model decides what happens next,
            and every run keeps a report.
          </p>
          {workflows.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">
              No workflows have been published yet.
            </p>
          ) : (
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {workflows.map((t) => (
                <Link
                  key={t.name}
                  href={`/studio/configure/${t.name}`}
                  className="studio-section group p-5 no-underline transition hover:border-primary/40 sm:p-5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground">
                        <Icon name="swap" size={18} />
                      </span>
                      <div className="min-w-0">
                        <h3 className="truncate text-[15px] font-semibold text-ink group-hover:text-primary">
                          {t.name
                            .replaceAll("_", " ")
                            .replace(/^./, (c) => c.toUpperCase())}
                        </h3>
                        <p className="mt-0.5 text-[13px] text-muted-foreground">
                          Choose your source, destination and date range.
                        </p>
                      </div>
                    </div>
                    <span className="shrink-0 rounded-full border border-border bg-muted px-2.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      v{t.version}
                    </span>
                  </div>
                  <div className="mt-5 flex items-center justify-between border-t border-border pt-3.5 text-xs text-muted-foreground">
                    <span>{t.step_count} governed steps</span>
                    <span className="inline-flex items-center gap-1 font-medium text-primary">
                      Open workflow
                      <Icon name="arrowRight" size={13} width={2} />
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}

          <p className="mt-6 rounded-xl border border-dashed border-border p-4 text-sm leading-6 text-muted-foreground">
            <span className="font-medium text-ink">Task agents</span> — the
            async tier, where a model plans the work inside the governed loop
            and pauses for your approval — are not built yet. The governed loop
            they would run inside is finished; the planning tier is not, so
            there is nothing here to create.
          </p>

          {legacy.length > 0 && (
            <div className="mt-8">
              <h2 className="text-[15px] font-semibold text-ink">
                Legacy phone deployments
              </h2>
              <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
                Records from before the agent workspace. Pause one before
                connecting its number to a new agent.
              </p>
              <div className="studio-section mt-4 p-5 sm:p-5">
                {legacy.map((i) => (
                  <div
                    key={i.instance_id}
                    className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3.5 first:pt-0 last:border-0 last:pb-0"
                  >
                    <span className="text-sm text-ink">
                      {i.template}
                      <span aria-hidden className="mx-2 text-faint">
                        ·
                      </span>
                      <span className="font-mono text-[12px] text-muted-foreground">
                        {i.phone_number}
                      </span>
                    </span>
                    <button
                      className="studio-secondary"
                      onClick={async () => {
                        try {
                          await api.pauseInstance(i.instance_id);
                          setLegacy(
                            legacy.filter(
                              (x) => x.instance_id !== i.instance_id,
                            ),
                          );
                        } catch (e) {
                          setError(e instanceof Error ? e.message : String(e));
                        }
                      }}
                    >
                      Pause legacy deployment
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
