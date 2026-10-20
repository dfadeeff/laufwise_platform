"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail, useWorkspaceModules } from "@/components/studio/WorkspaceShell";
import type { AgentConfig, StudioAgent } from "@/features/agents/types";
import type { Approval, PracticeType } from "@/types";
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
  WorkflowAgent,
  WorkflowKind,
} from "@/types";

// Everything this workspace has built, and how it is doing, in the two kinds of agent it can
// create: conversational agents talk to a caller in real time; workflow agents run a fixed
// procedure over a piece of work, case by case (ADR-0030). A model planning its own steps (the
// task tier) is not built, and is not offered.
//
// A workspace chooses the kind of agent, never a domain first (ADR-0031): creating an agent adds
// the module it comes from, and the server holds the workspace to what it has created.
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
    practice_type: string | null;
  }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("Empfang");
  const [practice, setPractice] = useState("");
  const [locale, setLocale] = useState<AgentConfig["locale"]>("de");
  const [types, setTypes] = useState<PracticeType[]>([]);
  const [type, setType] = useState("");
  useEffect(() => {
    // A blank agent still works if this fails; the picker just offers nothing.
    api.listPracticeTypes().then(setTypes).catch(() => setTypes([]));
  }, []);
  const chosen = types.find((t) => t.key === type);
  return (
    <form
      className="studio-section mt-6"
      onSubmit={(e) => {
        e.preventDefault();
        onCreate({
          name: name.trim(),
          practice_name: practice.trim(),
          locale,
          practice_type: type || null,
        });
      }}
    >
      <h2 className="text-lg font-semibold tracking-tight text-ink">
        New conversational agent
      </h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Two details to start with. Everything else — hours, documents, your
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
        <label className="block sm:col-span-2">
          <span className="studio-label">Start from</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="studio-input mt-1.5"
          >
            <option value="">A blank agent</option>
            {types.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
          {chosen && (
            <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
              {chosen.description} Sets opening hours, appointment length and conversation
              style; add your services and prices as documents.
            </span>
          )}
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
/** What a workspace with nothing in it is offered: the two kinds of agent (ADR-0030 D1). The
 *  kind comes first; a domain only appears later, as the workflow or setup chosen (ADR-0031). */
function ChooseAgentType({
  busy,
  onConversational,
  onWorkflow,
}: {
  busy: boolean;
  onConversational: () => void;
  onWorkflow: () => void;
}) {
  const kinds = [
    {
      icon: "phone",
      title: "Conversational agent",
      text: "Talks with people in real time, and acts only through governed steps.",
      start: onConversational,
    },
    {
      icon: "swap",
      title: "Workflow agent",
      text: "Runs a fixed sequence of steps over a piece of work, and checks the result.",
      start: onWorkflow,
    },
  ] as const;
  return (
    <section className="mt-8">
      <h2 className="text-[15px] font-semibold text-ink">Create your first agent</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {kinds.map((k) => (
          <button
            key={k.title}
            onClick={k.start}
            disabled={busy}
            className="studio-section group flex flex-col p-5 text-left transition hover:border-primary/40 sm:p-5"
          >
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-primary">
              <Icon name={k.icon} size={18} />
            </span>
            <h3 className="mt-4 text-[15px] font-semibold text-ink group-hover:text-primary">
              {k.title}
            </h3>
            <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{k.text}</p>
          </button>
        ))}
      </div>
    </section>
  );
}

/** Create a workflow agent from one of the workspace's workflows: a name, then its page. */
function NewWorkflowAgent({
  kinds,
  busy,
  onCreate,
  onCancel,
}: {
  kinds: WorkflowKind[];
  busy: boolean;
  onCreate: (workflow: string, name: string) => void;
  onCancel: () => void;
}) {
  const [workflow, setWorkflow] = useState(kinds[0]?.key ?? "");
  const [name, setName] = useState("");
  const chosen = kinds.find((k) => k.key === workflow);
  return (
    <form
      className="studio-section mt-4 p-5 sm:p-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (workflow && name.trim()) onCreate(workflow, name.trim());
      }}
    >
      <h3 className="text-[15px] font-semibold text-ink">New workflow agent</h3>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">Workflow</span>
          <select
            value={workflow}
            onChange={(e) => setWorkflow(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-white px-3 text-[13px] text-ink"
          >
            {kinds.map((k) => (
              <option key={k.key} value={k.key}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Abrechnungen Lindenstraße 14"
            className="h-9 w-full rounded-lg border border-border bg-white px-3 text-[13px] text-ink outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
          />
        </label>
      </div>
      {chosen && <p className="mt-3 text-sm text-muted-foreground">{chosen.summary}</p>}
      <div className="mt-5 flex gap-2">
        <button className="studio-primary" disabled={busy || !name.trim()}>
          Create workflow agent
        </button>
        <button type="button" className="studio-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
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
  const { modules, error: modulesError } = useWorkspaceModules();
  const [agents, setAgents] = useState<StudioAgent[] | null>(null);
  const [calls, setCalls] = useState<ConversationSummary[] | null>(null);
  const [workflows, setWorkflows] = useState<TemplateSummary[]>([]);
  const [legacy, setLegacy] = useState<InstanceSummary[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [query, setQuery] = useState("");
  const [workflowAgents, setWorkflowAgents] = useState<WorkflowAgent[]>([]);
  const [waiting, setWaiting] = useState<Approval[]>([]);
  const [kinds, setKinds] = useState<WorkflowKind[]>([]);
  const [creatingWorkflow, setCreatingWorkflow] = useState(false);

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
    Promise.all([api.listWorkflowAgents(), api.listWorkflowKinds()])
      .then(([rows, offered]) => {
        setWorkflowAgents(rows);
        setKinds(offered);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);
  useEffect(() => {
    // What waits for this person's decision (ADR-0034). Allowed to fail on its own: the list of
    // agents matters more than this reminder.
    api
      .listApprovals()
      .then((all) => setWaiting(all.filter((a) => a.can_decide)))
      .catch(() => setWaiting([]));
  }, []);

  const createWorkflowAgent = async (workflow: string, name: string) => {
    setBusy(true);
    setError("");
    try {
      const agent = await api.createWorkflowAgent(workflow, name);
      router.push(`/studio/workflow-agents/${agent.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const create = async (seed: {
    name: string;
    practice_name: string;
    locale: AgentConfig["locale"];
    practice_type: string | null;
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

  const running = modules?.running ?? [];
  // A template a module owns is shown only where that module runs; one no module claims was
  // authored in the Studio and is shown everywhere, as before.
  const owned = new Set(modules?.available.flatMap((m) => m.templates) ?? []);
  const ours = new Set(running.flatMap((m) => m.templates));
  const shownWorkflows = workflows.filter((t) => ours.has(t.name) || !owned.has(t.name));
  // Nothing here yet and nothing being created: offer the two kinds of agent.
  const pristine =
    agents !== null &&
    agents.length === 0 &&
    workflowAgents.length === 0 &&
    shownWorkflows.length === 0 &&
    !creating &&
    !creatingWorkflow;

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
            Everything this workspace runs, and how it is doing.
          </p>
        </div>
      </div>

      {waiting.length > 0 && (
        <div className="mt-6 rounded-xl border border-warning/25 bg-warning/10 p-4 text-sm text-ink">
          <p className="font-semibold">Waiting for your approval</p>
          <ul className="mt-2 space-y-1">
            {waiting.map((a) =>
              a.case ? (
                <li key={a.id}>
                  <Link href={`/studio/cases/${a.case.id}`} className="text-primary underline">
                    {a.case.title}
                  </Link>{" "}
                  <span className="text-muted-foreground">
                    · letters to {a.letters.map((l) => l.recipient).join(", ")}
                  </span>
                </li>
              ) : null,
            )}
          </ul>
        </div>
      )}

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

      {modulesError && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
        >
          {modulesError}
        </div>
      )}

      {pristine && (
        <ChooseAgentType
          busy={busy}
          onConversational={() => setCreating(true)}
          onWorkflow={() => setCreatingWorkflow(true)}
        />
      )}

      {agents && !pristine && (
        <div className="mt-10 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-3">
          <div>
            <h2 className="text-lg font-semibold text-ink">Conversational agents</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              Talk with callers in real time, and act only through governed steps.
            </p>
          </div>
          {!creating && (
            <button
              onClick={() => setCreating(true)}
              disabled={busy}
              className="studio-primary"
            >
              <Icon name="plus" size={15} width={2.2} />
              New conversational agent
            </button>
          )}
        </div>
      )}

      {agents && !pristine && agents.length === 0 && !creating && (
        <p className="mt-4 text-sm text-muted-foreground">
          No conversational agents yet.
        </p>
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
                      "",
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
                        // The whole row opens the agent: a name styled as plain text was the only
                        // way in, and nobody could tell it was a link.
                        onClick={() => router.push(`/studio/agents/${agent.id}/overview`)}
                        className="cursor-pointer border-b border-border last:border-0 hover:bg-surface"
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
                        <td className="px-4 py-3 text-right">
                          <Link
                            href={`/studio/agents/${agent.id}/overview`}
                            onClick={(e) => e.stopPropagation()}
                            className="studio-secondary whitespace-nowrap no-underline"
                          >
                            Edit →
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                  {shown.length === 0 && (
                    <tr>
                      <td
                        colSpan={7}
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

      {agents && !pristine && (
        <section className="mt-12">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-3">
            <div>
              <h2 className="text-lg font-semibold text-ink">Workflow agents</h2>
              <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                A fixed sequence of steps over a piece of work, triggered rather
                than spoken to. No model decides what happens next, and every
                case keeps its history.
              </p>
            </div>
            {kinds.length > 0 && !creatingWorkflow && (
              <button
                onClick={() => setCreatingWorkflow(true)}
                disabled={busy}
                className="studio-primary"
              >
                <Icon name="plus" size={15} width={2.2} />
                New workflow agent
              </button>
            )}
          </div>

          {creatingWorkflow && (
            <NewWorkflowAgent
              kinds={kinds}
              busy={busy}
              onCreate={createWorkflowAgent}
              onCancel={() => setCreatingWorkflow(false)}
            />
          )}

          {workflowAgents.length > 0 && (
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {workflowAgents.map((a) => (
                <Link
                  key={a.id}
                  href={`/studio/workflow-agents/${a.id}`}
                  className="studio-section group p-5 no-underline transition hover:border-primary/40 sm:p-5"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground">
                      <Icon name="file" size={18} />
                    </span>
                    <div className="min-w-0">
                      <h3 className="truncate text-[15px] font-semibold text-ink group-hover:text-primary">
                        {a.name}
                      </h3>
                      <p className="mt-0.5 text-[13px] text-muted-foreground">
                        {a.workflow.label}
                      </p>
                    </div>
                  </div>
                  <div className="mt-5 flex items-center justify-end border-t border-border pt-3.5 text-xs">
                    <span className="inline-flex items-center gap-1 font-medium text-primary">
                      Open cases
                      <Icon name="arrowRight" size={13} width={2} />
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}

          {workflowAgents.length === 0 && !creatingWorkflow && kinds.length > 0 && (
            <p className="mt-4 text-sm text-muted-foreground">
              No workflow agents yet. Create one from{" "}
              {kinds.map((k) => k.label).join(", ")}.
            </p>
          )}

          {shownWorkflows.length === 0 ? (
            kinds.length === 0 && (
              <p className="mt-4 text-sm text-muted-foreground">
                No workflows in this workspace yet.
              </p>
            )
          ) : (
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {shownWorkflows.map((t) => (
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
