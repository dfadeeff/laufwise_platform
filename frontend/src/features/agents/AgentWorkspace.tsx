"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { api } from "@/lib/api";
import type { AgentConfig, StudioAgent } from "./types";
import { ConfigEditor } from "./ConfigEditor";
import { PhoneSetup } from "./PhoneSetup";
import { Section } from "./Fields";
import { VoiceTest } from "./VoiceTest";
const sections = [
  ["overview", "Overview"],
  ["instructions", "Instructions"],
  ["knowledge", "Practice knowledge"],
  ["capabilities", "Capabilities"],
  ["voice", "Voice & language"],
  ["tests", "Tests"],
  ["phone", "Phone & handoff"],
  ["history", "History"],
];
export function AgentWorkspace({ agentId }: { agentId: string }) {
  const section = usePathname().split("/")[4] || "overview";
  const router = useRouter();
  const [agent, setAgent] = useState<StudioAgent | null>(null);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(false);
  const dirty =
    !!agent && JSON.stringify(config) !== JSON.stringify(agent.config);
  useEffect(() => {
    let cancelled = false;
    api
      .getAgent(agentId)
      .then((a) => {
        if (!cancelled) {
          setAgent(a);
          setConfig(a.config);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    const guard = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest?.("a");
      if (
        dirty &&
        link &&
        !link.href.includes(`/studio/agents/${agentId}/`) &&
        !window.confirm("Leave this agent without saving your changes?")
      ) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    document.addEventListener("click", guard, true);
    return () => document.removeEventListener("click", guard, true);
  }, [dirty, agentId]);
  const adopt = (a: StudioAgent) => {
    setAgent(a);
    setConfig(a.config);
  };
  async function save() {
    if (!agent || !config) return;
    setBusy(true);
    setError("");
    try {
      const a = dirty
        ? await api.saveAgent(agent.id, agent.generation, config)
        : agent;
      adopt(a);
      setNotice("Draft saved. Live calls are unchanged.");
      return a;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    const a = await save();
    if (a) router.push(`/studio/agents/${agentId}/tests`);
  }
  async function publish() {
    if (!agent) return;
    setBusy(true);
    setError("");
    try {
      adopt(await api.publishAgent(agent.id, agent.generation));
      setReview(false);
      setNotice(
        "Revision published. Choose it in Phone & handoff when you are ready to activate.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const change = (patch: Partial<AgentConfig>) => {
    setConfig((c) => (c ? { ...c, ...patch } : c));
    setNotice("");
  };
  if (!agent || !config)
    return (
      <div className="p-8 text-sm">
        {error ? (
          <p role="alert" className="text-danger">
            {error}{" "}
            <button
              className="underline"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </p>
        ) : (
          "Opening your agent…"
        )}
      </div>
    );
  const title = sections.find(([key]) => key === section)?.[1] ?? "Overview";
  const published = agent.history.find(
    (r) => r.id === agent.published_instance_id,
  );
  return (
    <div>
      <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b border-border bg-white/95 px-5 py-4 backdrop-blur-sm sm:px-8">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Link href="/studio" className="hover:text-primary">
              Agents
            </Link>
            <span>/</span>
            <span>{agent.channel?.active ? "Live" : "Not live"}</span>
          </div>
          <h1 className="mt-1 truncate text-lg font-semibold tracking-tight">
            {config.name}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            aria-live="polite"
            className="mr-2 text-xs text-muted-foreground"
          >
            {busy ? "Saving…" : dirty ? "Unsaved changes" : "Draft saved"}
          </span>
          <button
            className="studio-secondary"
            disabled={busy || !dirty}
            onClick={save}
          >
            Save
          </button>
          <button className="studio-secondary" disabled={busy} onClick={test}>
            ▷ Test
          </button>
          <button
            className="studio-primary"
            disabled={busy || dirty}
            title={dirty ? "Save your draft first" : undefined}
            onClick={() => setReview(true)}
          >
            Publish changes
          </button>
        </div>
      </header>
      <div className="min-w-0 md:flex">
        <aside className="border-b border-border bg-white p-3 md:w-48 md:shrink-0 md:border-b-0 md:border-r xl:w-52">
          <nav
            aria-label="Agent sections"
            className="flex gap-1 overflow-x-auto md:sticky md:top-28 md:flex-col"
          >
            {sections.map(([key, label]) => (
              <Link
                key={key}
                href={`/studio/agents/${agentId}/${key}`}
                aria-current={key === section ? "page" : undefined}
                className={`whitespace-nowrap rounded-lg px-3 py-2.5 text-sm ${key === section ? "bg-accent/60 font-medium text-primary" : "text-muted-foreground hover:bg-muted"}`}
              >
                {label}
              </Link>
            ))}
          </nav>
        </aside>
        <main className="min-w-0 flex-1 px-5 py-7 sm:px-8">
          <div className="mx-auto max-w-3xl">
            <div className="mb-6">
              <p className="text-xs text-muted-foreground">
                {config.practice_name || "Practice setup"}
              </p>
              <h2 className="mt-1 text-2xl font-semibold tracking-tight">
                {title}
              </h2>
            </div>
            {error && (
              <p
                role="alert"
                className="mb-5 rounded-lg bg-danger/10 p-4 text-sm text-danger"
              >
                {error}
              </p>
            )}
            {notice && (
              <p
                role="status"
                className="mb-5 rounded-lg bg-success/10 p-4 text-sm text-success"
              >
                {notice}
              </p>
            )}
            {review && (
              <Section
                title="Review publication"
                description="This creates an immutable revision. Your live phone assignment is not changed."
              >
                <p className="text-sm">
                  Publishing <strong>{config.name}</strong> for{" "}
                  <strong>{config.practice_name || "your practice"}</strong>,
                  with {config.treatments.length} treatments.{" "}
                  {config.booking_enabled
                    ? "Booking enabled."
                    : "Booking disabled."}
                </p>
                {agent.issues.length > 0 ? (
                  <ul className="space-y-2 text-sm text-warning">
                    {agent.issues.map((issue) => (
                      <li key={issue}>• {issue}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-success">
                    Required practice details are complete.
                  </p>
                )}
                <p className="text-xs leading-5 text-muted-foreground">
                  Publication checks configuration. It does not certify audio
                  quality or a real Thevea booking.
                </p>
                <div className="flex gap-3">
                  <button
                    className="studio-primary"
                    disabled={busy || !!agent.issues.length}
                    onClick={publish}
                  >
                    Publish revision
                  </button>
                  <button
                    className="studio-secondary"
                    onClick={() => setReview(false)}
                  >
                    Cancel
                  </button>
                </div>
              </Section>
            )}
            <fieldset disabled={busy} className="mt-5 min-w-0 space-y-6">
              {section === "overview" && (
                <>
                  <Section
                    title={
                      agent.channel?.active
                        ? "Your receptionist is live"
                        : "Let’s get your receptionist ready"
                    }
                    description={
                      agent.channel?.active
                        ? `Answering ${agent.channel.phone_number}. Saved drafts do not affect current calls.`
                        : "Set up the essentials, try a conversation, and connect your phone when you’re ready."
                    }
                  >
                    <div className="grid gap-4 sm:grid-cols-3">
                      {[
                        ["Agent", "Voice receptionist"],
                        [
                          "Published",
                          published
                            ? `Revision ${published.revision}`
                            : "Not yet",
                        ],
                        [
                          "Phone",
                          agent.channel?.active
                            ? "Answering calls"
                            : "Not answering",
                        ],
                      ].map(([label, value]) => (
                        <div key={label} className="rounded-lg bg-surface p-4">
                          <p className="text-xs text-muted-foreground">
                            {label}
                          </p>
                          <p className="mt-2 text-sm font-medium">{value}</p>
                        </div>
                      ))}
                    </div>
                  </Section>
                  <Section title="Your setup checklist">
                    {[
                      [
                        "knowledge",
                        "Practice details",
                        !!config.practice_name && !!config.treatments.length,
                        "Add your address, opening hours and treatments.",
                      ],
                      [
                        "instructions",
                        "A helpful welcome",
                        !!config.greeting,
                        "Choose a greeting and conversation style.",
                      ],
                      [
                        "capabilities",
                        "Booking permissions",
                        !!config.consent_policy_id,
                        "Review required checks and your privacy policy.",
                      ],
                      [
                        "phone",
                        "Calendar & staff handoff",
                        !!agent.channel,
                        "Connect Thevea and tell us who should receive call summaries.",
                      ],
                      [
                        "tests",
                        "Try it yourself",
                        false,
                        "Rehearse with an isolated calendar before publishing.",
                      ],
                    ].map(([key, label, done, desc]) => (
                      <Link
                        key={String(key)}
                        href={`/studio/agents/${agent.id}/${key}`}
                        className="flex items-start gap-4 border-b border-border pb-4 last:border-0 last:pb-0"
                      >
                        <span
                          className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${done ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
                        >
                          {done ? "✓" : "→"}
                        </span>
                        <div>
                          <p className="text-sm font-medium">{label}</p>
                          <p className="mt-1 text-sm leading-6 text-muted-foreground">
                            {desc}
                          </p>
                        </div>
                      </Link>
                    ))}
                  </Section>
                </>
              )}
              <ConfigEditor section={section} config={config} change={change} />
              {section === "phone" && (
                <PhoneSetup
                  key={agent.published_instance_id}
                  agent={agent}
                  config={config}
                  change={change}
                  updated={(a) => {
                    setAgent(a);
                  }}
                />
              )}
              {section === "tests" &&
                (dirty ? (
                  <Section
                    title="Save before testing"
                    description="A rehearsal uses the saved draft so its results refer to an exact configuration."
                  >
                    <button className="studio-primary" onClick={save}>
                      Save draft
                    </button>
                  </Section>
                ) : (
                  <VoiceTest
                    key={agent.generation}
                    agentId={agent.id}
                    generation={agent.generation}
                    initialLanguage={config.locale}
                  />
                ))}
              {section === "history" && (
                <Section
                  title="Published revisions"
                  description="Past revisions remain unchanged. Restore one to your draft to review it, or select it in Phone & handoff to roll back live calls."
                >
                  {!agent.history.length && (
                    <p className="text-sm text-muted-foreground">
                      No published revisions yet. Your draft is saved
                      separately.
                    </p>
                  )}
                  {agent.history.map((r) => (
                    <div
                      className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4"
                      key={r.id}
                    >
                      <div>
                        <p className="text-sm font-medium">
                          Revision {r.revision}
                          {agent.channel?.instance_id === r.id &&
                          agent.channel.active
                            ? " · Live"
                            : ""}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {new Date(r.created_at).toLocaleString()}
                        </p>
                      </div>
                      <button
                        className="studio-secondary"
                        onClick={() => {
                          const {
                            contracts: _,
                            base_prompt: _prompt,
                            ...rest
                          } = r.config as AgentConfig & {
                            contracts?: unknown;
                            base_prompt?: string;
                          };
                          setConfig(rest);
                          setNotice(
                            "Revision restored to the editor. Save to update your draft.",
                          );
                        }}
                      >
                        Restore to draft
                      </button>
                    </div>
                  ))}
                </Section>
              )}
            </fieldset>
          </div>
        </main>
      </div>
    </div>
  );
}
