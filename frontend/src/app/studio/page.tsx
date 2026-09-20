"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AgentConfig, StudioAgent } from "@/features/agents/types";
import type { InstanceSummary, TemplateSummary } from "@/types";

// Everything this practice has built, grouped by what kind of agent it is. The three tiers are
// real and different: a voice receptionist talks to a caller, a workflow runs a fixed procedure,
// and the task tier — a model planning inside the governed loop — is not built yet. We say so
// rather than showing a create button that leads nowhere.

const LANGUAGES: [AgentConfig["locale"], string][] = [
  ["de", "Deutsch"],
  ["en", "English"],
  ["ru", "Русский"],
  ["ar", "العربية"],
];

function Group({
  title,
  description,
  count,
  children,
}: {
  title: string;
  description: string;
  count?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
          {title}
        </h2>
        {count && <span className="text-xs text-muted-foreground">{count}</span>}
      </div>
      <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
        {description}
      </p>
      <div className="mt-4">{children}</div>
    </section>
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
      <h2 className="text-lg font-semibold tracking-tight">New voice agent</h2>
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
            onChange={(e) =>
              setLocale(e.target.value as AgentConfig["locale"])
            }
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

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<StudioAgent[] | null>(null);
  const [workflows, setWorkflows] = useState<TemplateSummary[]>([]);
  const [legacy, setLegacy] = useState<InstanceSummary[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
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
  return (
    <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-10">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-widest text-primary">
            Your team, extended
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-ink">
            Agents
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            A helpful first conversation. A booking your practice can trust.
          </p>
        </div>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            disabled={busy}
            className="studio-primary"
          >
            + New voice agent
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
      {agents && (
        <>
          <Group
            title="Voice receptionists"
            description="Real-time conversations on the phone or in the browser. Every booking is verified by the engine before the caller is told it happened."
            count={
              agents.length
                ? `${agents.filter((a) => a.channel?.active).length} live · ${agents.length} total`
                : undefined
            }
          >
            {agents.length === 0 ? (
              <section className="studio-section grid items-center gap-8 lg:grid-cols-2">
                <div>
                  <div className="mb-6 grid h-14 w-14 place-items-center rounded-2xl bg-accent text-2xl text-primary">
                    ◈
                  </div>
                  <h3 className="text-2xl font-semibold tracking-tight">
                    Meet your new receptionist
                  </h3>
                  <p className="mt-3 max-w-md text-sm leading-7 text-muted-foreground">
                    Answer common questions, help patients find a time, and send
                    your team the details. Set it up at your own pace before
                    connecting your phone.
                  </p>
                  {!creating && (
                    <button
                      onClick={() => setCreating(true)}
                      disabled={busy}
                      className="studio-primary mt-6"
                    >
                      Set up your first agent →
                    </button>
                  )}
                </div>
                <ol className="space-y-5 rounded-xl bg-surface p-6">
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
                      <span className="pt-0.5 text-sm font-medium text-primary">
                        {n}
                      </span>
                      <div>
                        <h4 className="text-sm font-medium">{t}</h4>
                        <p className="mt-1 text-sm leading-6 text-muted-foreground">
                          {d}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            ) : (
              <div className="grid gap-5 xl:grid-cols-2">
                {agents.map((agent) => (
                  <Link
                    key={agent.id}
                    href={`/studio/agents/${agent.id}/overview`}
                    className="studio-section group transition hover:border-primary/40 hover:shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="grid h-11 w-11 place-items-center rounded-xl bg-accent text-xl text-primary">
                        ◈
                      </div>
                      <span
                        className={`rounded-full px-2.5 py-1 text-xs font-medium ${agent.channel?.active ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
                      >
                        {agent.channel?.active
                          ? "Live"
                          : agent.published_instance_id
                            ? "Published · not live"
                            : "Draft"}
                      </span>
                    </div>
                    <h3 className="mt-5 text-lg font-semibold group-hover:text-primary">
                      {agent.config.name}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {agent.config.practice_name || "Practice setup needed"}
                    </p>
                    <div className="mt-6 flex justify-between border-t border-border pt-4 text-xs text-muted-foreground">
                      <span>
                        {agent.channel?.phone_number || "No phone connected"}
                      </span>
                      <span>Open workspace →</span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </Group>

          <Group
            title="Workflows & procedures"
            description="A fixed sequence of steps, triggered rather than spoken to — calendar imports and the like. No model decides what happens next, and every run keeps a report."
            count={workflows.length ? `${workflows.length} available` : undefined}
          >
            {workflows.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No workflows have been published yet.
              </p>
            ) : (
              <div className="grid gap-5 xl:grid-cols-2">
                {workflows.map((t) => (
                  <Link
                    key={t.name}
                    href={`/studio/configure/${t.name}`}
                    className="studio-section group transition hover:border-primary/40 hover:shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="grid h-11 w-11 place-items-center rounded-xl bg-accent text-xl text-primary">
                        ⇄
                      </div>
                      <span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                        v{t.version}
                      </span>
                    </div>
                    <h3 className="mt-5 text-lg font-semibold group-hover:text-primary">
                      {t.name
                        .replaceAll("_", " ")
                        .replace(/^./, (c) => c.toUpperCase())}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Choose your source, destination and date range.
                    </p>
                    <div className="mt-6 flex justify-between border-t border-border pt-4 text-xs text-muted-foreground">
                      <span>{t.step_count} governed steps</span>
                      <span>Open workflow →</span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </Group>

          <Group
            title="Task agents"
            description="The async tier: a model plans the work inside the governed loop, pauses for your approval, and continues over hours or days."
          >
            <p className="rounded-xl border border-dashed border-border p-5 text-sm text-muted-foreground">
              Not built yet. The governed loop these would run inside is
              finished; the planning tier is not, so there is nothing here to
              create.
            </p>
          </Group>

          {legacy.length > 0 && (
            <Group
              title="Legacy phone deployments"
              description="Records from before the agent workspace. Pause one before connecting its number to a new agent."
            >
              <div className="studio-section">
                {legacy.map((i) => (
                  <div
                    key={i.instance_id}
                    className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-4 first:pt-0 last:border-0 last:pb-0"
                  >
                    <span className="text-sm">
                      {i.template} · {i.phone_number}
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
            </Group>
          )}
        </>
      )}
    </main>
  );
}
