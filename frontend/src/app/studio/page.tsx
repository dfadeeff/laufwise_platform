"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail, useWorkspaceModules } from "@/components/studio/WorkspaceShell";
import type { AgentConfig, StudioAgent } from "@/features/agents/types";
import type {
  Approval,
  InstanceSummary,
  PracticeType,
  TemplateSummary,
  WorkflowAgent,
  WorkflowKind,
  WorkspaceModule,
} from "@/types";

// The workspace's agents, in the two kinds it can create: conversational agents talk with people
// in real time; workflow automations run a fixed sequence of steps over a piece of work, with no
// model deciding the path (ADR-0030). Which of the two a workspace can create comes from its
// modules (ADR-0028). Kept minimal on purpose: one place to create, one list of what exists;
// call figures live on the Dashboard, a vertical's onboarding inside its own creation flow.

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
        New voice agent
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

const humanize = (name: string) =>
  name.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

/** Every workflow automation the workspace can start: a module's case-by-case workflows become a
 *  named agent; a built-in runbook (calendar import, mirror) opens its own setup page. */
/** Which offered module a workflow or built-in template belongs to, for labelling a mixed list. */
function moduleLabel(offered: WorkspaceModule[], key: string): string | undefined {
  return offered.find(
    (m) => m.templates.includes(key) || m.workflows.some((w) => w.key === key),
  )?.label;
}

function NewWorkflowAutomation({
  kinds,
  builtins,
  moduleOf,
  busy,
  onCreate,
  onCancel,
}: {
  kinds: WorkflowKind[];
  builtins: TemplateSummary[];
  moduleOf?: (key: string) => string | undefined;
  busy: boolean;
  onCreate: (workflow: string, name: string) => void;
  onCancel: () => void;
}) {
  const router = useRouter();
  const options = [
    ...kinds.map((k) => ({
      value: `wf:${k.key}`,
      label: k.label + (moduleOf?.(k.key) ? ` · ${moduleOf(k.key)}` : ""),
      summary: k.summary,
    })),
    ...builtins.map((t) => ({
      value: `tpl:${t.name}`,
      label: humanize(t.name) + (moduleOf?.(t.name) ? ` · ${moduleOf(t.name)}` : ""),
      summary: "Set up its source, destination and range on its own page.",
    })),
  ];
  const [choice, setChoice] = useState(options[0]?.value ?? "");
  const [name, setName] = useState("");
  const chosen = options.find((o) => o.value === choice);
  const isTemplate = choice.startsWith("tpl:");
  return (
    <form
      className="studio-section mt-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (isTemplate) router.push(`/studio/configure/${choice.slice(4)}`);
        else if (name.trim()) onCreate(choice.slice(3), name.trim());
      }}
    >
      <h2 className="text-lg font-semibold tracking-tight text-ink">New workflow automation</h2>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="studio-label">Workflow</span>
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="studio-input mt-1.5"
          >
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {!isTemplate && (
          <label className="block">
            <span className="studio-label">Name</span>
            <input
              autoFocus
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="studio-input mt-1.5"
              placeholder="e.g. Abrechnungen Lindenstraße 14"
            />
          </label>
        )}
      </div>
      {chosen && <p className="mt-3 text-sm text-muted-foreground">{chosen.summary}</p>}
      <div className="mt-6 flex flex-wrap gap-3">
        <button type="submit" disabled={busy || (!isTemplate && !name.trim())} className="studio-primary">
          {isTemplate ? "Open setup" : busy ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="studio-secondary">
          Cancel
        </button>
      </div>
    </form>
  );
}

type Kind = "voice" | "workflow";

/** The first step of "New agent": which of the two kinds. A kind the workspace's modules do not
 *  offer is shown, but cannot be chosen, and says why. */
function ChooseKind({
  voice,
  workflow,
  onChoose,
  onCancel,
}: {
  voice: boolean;
  workflow: boolean;
  onChoose: (kind: Kind) => void;
  onCancel: () => void;
}) {
  const kinds = [
    {
      key: "voice" as const,
      icon: "phone" as const,
      title: "Conversational",
      text: "Talks with people in real time, and acts only through governed steps.",
      available: voice,
    },
    {
      key: "workflow" as const,
      icon: "swap" as const,
      title: "Workflow automation",
      text: "Runs a fixed sequence of steps over a piece of work. No model decides the path.",
      available: workflow,
    },
  ];
  return (
    <section className="studio-section mt-6">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-ink">
          What kind of agent?
        </h2>
        <button type="button" onClick={onCancel} className="studio-secondary">
          Cancel
        </button>
      </div>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        {kinds.map((k) => (
          <button
            key={k.key}
            type="button"
            disabled={!k.available}
            onClick={() => onChoose(k.key)}
            className="flex flex-col items-start rounded-xl border border-border bg-white p-5 text-left transition enabled:hover:border-primary/50 enabled:hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-muted text-muted-foreground">
              <Icon name={k.icon} size={18} />
            </span>
            <span className="mt-4 text-[15px] font-semibold text-ink">{k.title}</span>
            <span className="mt-1.5 text-sm leading-6 text-muted-foreground">{k.text}</span>
            {!k.available && (
              <span className="mt-3 text-[13px] text-faint">
                Not offered by this workspace&apos;s modules yet.
              </span>
            )}
          </button>
        ))}
      </div>
    </section>
  );
}

interface Row {
  key: string;
  name: string;
  detail: string;
  kind: "Conversational" | "Workflow automation";
  status: React.ReactNode;
  href: string;
}

export default function AgentsPage() {
  const router = useRouter();
  const { modules, error: modulesError, refresh } = useWorkspaceModules();
  const [agents, setAgents] = useState<StudioAgent[] | null>(null);
  const [workflowAgents, setWorkflowAgents] = useState<WorkflowAgent[]>([]);
  const [kinds, setKinds] = useState<WorkflowKind[]>([]);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [instances, setInstances] = useState<InstanceSummary[]>([]);
  // null: not creating; "choose": picking the kind; then the kind's own form.
  const [creating, setCreating] = useState<"choose" | Kind | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [waiting, setWaiting] = useState<Approval[]>([]);

  const load = () => {
    setError("");
    Promise.all([
      api.listAgents(),
      api.listWorkflowAgents(),
      api.listWorkflowKinds(),
      api.listTemplates(),
      api.listInstances(),
    ])
      .then(([voiceAgents, wfAgents, offered, allTemplates, allInstances]) => {
        setAgents(voiceAgents);
        setWorkflowAgents(wfAgents);
        setKinds(offered);
        // One entry per built-in workflow, at its newest published version.
        const latest = new Map<string, TemplateSummary>();
        for (const t of allTemplates)
          if (
            t.agent_class === "workflow" &&
            t.status === "published" &&
            (!latest.has(t.name) || latest.get(t.name)!.version < t.version)
          )
            latest.set(t.name, t);
        setTemplates([...latest.values()]);
        setInstances(allInstances);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);
  useEffect(() => {
    // Allowed to fail on its own: the list of agents matters more than this reminder.
    api
      .listApprovals()
      .then((all) => setWaiting(all.filter((a) => a.can_decide)))
      .catch(() => setWaiting([]));
  }, []);

  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      refresh(); // the first agent from a module records it, and the wording follows
      router.push(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const running = modules?.running ?? [];
  // What "New agent" offers: the workspace's own modules once it runs any, every module before.
  // There is no up-front choice of business; the first agent created records its module.
  const offered = running.length > 0 ? running : (modules?.available ?? []);
  const voice = offered.some((m) => m.surfaces.includes("voice_agents"));
  // A template a module owns is offered only where that module runs; one no module claims was
  // authored in the Studio and is offered everywhere.
  const owned = new Set(modules?.available.flatMap((m) => m.templates) ?? []);
  const ours = new Set(offered.flatMap((m) => m.templates));
  const builtins = templates.filter((t) => ours.has(t.name) || !owned.has(t.name));
  const builtinNames = new Set(builtins.map((t) => t.name));

  const rows: Row[] = [
    ...(agents ?? []).map((a) => ({
      key: `voice-${a.id}`,
      name: a.config.name,
      detail: a.config.practice_name,
      kind: "Conversational" as const,
      status: <StateChip state={stateOf(a)} />,
      href: `/studio/agents/${a.id}/overview`,
    })),
    ...workflowAgents.map((a) => ({
      key: `wf-${a.id}`,
      name: a.name,
      detail: a.workflow.label,
      kind: "Workflow automation" as const,
      status: <span className="text-[13px] text-muted-foreground">Active</span>,
      href: `/studio/workflow-agents/${a.id}`,
    })),
    ...instances
      .filter((i) => !i.agent_id && i.status === "deployed" && builtinNames.has(i.template))
      .map((i) => ({
        key: `tpl-${i.instance_id}`,
        name: humanize(i.template),
        detail: `v${i.template_version}${i.schedule ? " · scheduled" : ""}`,
        kind: "Workflow automation" as const,
        status: <span className="text-[13px] text-muted-foreground">Deployed</span>,
        href: `/studio/configure/${i.template}`,
      })),
  ];
  const q = query.trim().toLowerCase();
  const shown = rows.filter(
    (r) => !q || `${r.name} ${r.detail} ${r.kind}`.toLowerCase().includes(q),
  );
  const legacy = instances.filter((i) => !i.agent_id && i.phone_number && i.status === "deployed");

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Agents" }]} />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[28px] font-semibold tracking-tight text-ink">Agents</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {running.length === 1 ? running[0].intro : "Everything this workspace runs."}
          </p>
        </div>
        {modules && creating === null && (
          // What can be created is only known once the workspace's offer has loaded; before
          // that the chooser would call every kind "not offered".
          <button
            onClick={() => setCreating("choose")}
            disabled={busy || agents === null}
            className="studio-primary"
          >
            <Icon name="plus" size={15} width={2.2} />
            New agent
          </button>
        )}
      </div>

      {(error || modulesError) && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
        >
          {error || modulesError}
          {error && (
            <button className="ml-3 underline" onClick={load}>
              Retry
            </button>
          )}
        </div>
      )}

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

      {modules && (
        <>
          {creating === "choose" && (
            <ChooseKind
              voice={voice}
              workflow={kinds.length + builtins.length > 0}
              onChoose={setCreating}
              onCancel={() => setCreating(null)}
            />
          )}

          {creating === "voice" && (
            <NewVoiceAgent
              busy={busy}
              onCreate={(seed) =>
                run(async () => `/studio/agents/${(await api.createAgent(seed)).id}/overview`)
              }
              onCancel={() => setCreating(null)}
            />
          )}
          {creating === "workflow" && (
            <NewWorkflowAutomation
              kinds={kinds}
              builtins={builtins}
              moduleOf={offered.length > 1 ? (key) => moduleLabel(offered, key) : undefined}
              busy={busy}
              onCreate={(workflow, name) =>
                run(
                  async () =>
                    `/studio/workflow-agents/${(await api.createWorkflowAgent(workflow, name)).id}`,
                )
              }
              onCancel={() => setCreating(null)}
            />
          )}

          <section className="mt-8">
            {rows.length > 6 && (
              <label className="relative mb-4 block sm:w-64">
                <span className="sr-only">Search agents</span>
                <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint">
                  <Icon name="search" size={15} />
                </span>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search agents"
                  className="h-9 w-full rounded-lg border border-border bg-white pl-8 pr-3 text-[13px] text-ink outline-none placeholder:text-faint focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
            )}
            {!agents && !error ? (
              <p role="status" className="text-sm text-muted-foreground">
                Loading…
              </p>
            ) : rows.length === 0 ? (
              creating === null && (
                <div className="flex flex-col items-center rounded-xl border border-dashed border-border px-6 py-16 text-center">
                  <span className="grid h-11 w-11 place-items-center rounded-xl bg-muted text-muted-foreground">
                    <Icon name="cube" size={20} />
                  </span>
                  <p className="mt-4 text-[15px] font-semibold text-ink">No agents yet</p>
                  <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
                    A conversational agent talks with people; a workflow automation works
                    through a piece of work, step by step.
                  </p>
                  <button
                    onClick={() => setCreating("choose")}
                    className="studio-primary mt-5"
                  >
                    <Icon name="plus" size={15} width={2.2} />
                    Create your first agent
                  </button>
                </div>
              )
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {shown.map((r) => (
                  <Link
                    key={r.key}
                    href={r.href}
                    className="studio-section group flex flex-col p-5 no-underline transition hover:border-primary/40 sm:p-5"
                  >
                    <div className="flex items-start gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground">
                        <Icon name={r.kind === "Conversational" ? "phone" : "swap"} size={18} />
                      </span>
                      <div className="min-w-0">
                        <h3 className="truncate text-[15px] font-semibold text-ink group-hover:text-primary">
                          {r.name}
                        </h3>
                        <p className="truncate text-[13px] text-muted-foreground">
                          {r.detail || "\u00a0"}
                        </p>
                      </div>
                    </div>
                    <div className="mt-5 flex items-center justify-between gap-2 border-t border-border pt-3.5">
                      <span className="text-xs font-medium text-muted-foreground">{r.kind}</span>
                      {r.status}
                    </div>
                  </Link>
                ))}
                {shown.length === 0 && (
                  <p className="text-sm text-muted-foreground">No agents match this search.</p>
                )}
              </div>
            )}
          </section>

          {legacy.length > 0 && (
            <section className="mt-10">
              <h2 className="text-[15px] font-semibold text-ink">Legacy phone deployments</h2>
              <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
                Records from before the agent workspace. Pause one before connecting its number
                to a new agent.
              </p>
              <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-white">
                {legacy.map((i) => (
                  <li
                    key={i.instance_id}
                    className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                  >
                    <span className="text-sm text-ink">
                      {i.template}
                      <span className="ml-2 font-mono text-[12px] text-muted-foreground">
                        {i.phone_number}
                      </span>
                    </span>
                    <button
                      className="studio-secondary"
                      onClick={async () => {
                        try {
                          await api.pauseInstance(i.instance_id);
                          setInstances(instances.filter((x) => x.instance_id !== i.instance_id));
                        } catch (e) {
                          setError(e instanceof Error ? e.message : String(e));
                        }
                      }}
                    >
                      Pause legacy deployment
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  );
}
