"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import {
  StudioActions,
  StudioTrail,
} from "@/components/studio/WorkspaceShell";
import type { AgentConfig, StudioAgent } from "./types";
import type { ConversationSummary } from "@/types";
import { ConfigEditor } from "./ConfigEditor";
import { PhoneSetup } from "./PhoneSetup";
import { Section } from "./Fields";
import { VoiceTest } from "./VoiceTest";
import {
  bookedShare,
  callMetrics,
  CHANGE_MARK,
  draftChanges,
  instanceIds,
  type ConfigChange,
} from "./insights";

// The agent editor: what you are changing in the middle, where else you could be on the left,
// and what the change would mean on the right.
//
// The inspector is the part that earns its width. Editing here never touches a live call, which
// is the right guarantee and a confusing one — without something showing the distance between
// the draft in front of you and the revision answering the phone, "saved" and "in effect" blur
// together. So the right rail carries the diff, computed from the published revision itself.

const GROUPS: { group: string; items: [string, string][] }[] = [
  {
    group: "Build",
    items: [
      ["overview", "Overview"],
      ["instructions", "Instructions"],
      ["knowledge", "Knowledge base"],
      ["capabilities", "Capabilities"],
      ["voice", "Voice & language"],
    ],
  },
  { group: "Connect", items: [["phone", "Phone & handoff"]] },
  {
    group: "Verify",
    items: [
      ["tests", "Tests"],
      ["history", "History"],
    ],
  },
];

const SECTIONS = GROUPS.flatMap((g) => g.items);

/** The one line under each section title. It should say what the section is FOR, not repeat
 *  its name — the name is already three inches to the left in the tree. */
const ABOUT: Record<string, string> = {
  overview: "Where this agent stands, and what is left before it can answer a real call.",
  instructions: "What the agent knows before the phone rings.",
  knowledge: "The facts the agent says out loud and writes into an appointment.",
  capabilities: "What the agent may do. The runtime enforces this, not the prompt.",
  voice: "How the agent sounds, and the language it opens in.",
  phone: "The calendar it writes to, the team it hands off to, and the number it answers.",
  tests: "Rehearse against a synthetic calendar. Nothing here touches a patient record.",
  history: "Every published revision, kept exactly as it was published.",
};

const LANGUAGE: Record<AgentConfig["locale"], string> = {
  de: "Deutsch (DE)",
  en: "English (EN)",
  ru: "Русский (RU)",
  ar: "العربية (AR)",
};

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** "Mon–Fri", "Mon–Wed, Fri" — runs collapsed, because seven chips is not a summary. */
function openingDays(weekdays: number[]): string {
  const sorted = [...new Set(weekdays)].filter((d) => d >= 0 && d < 7).sort((a, b) => a - b);
  if (sorted.length === 0) return "No days selected";
  const runs: number[][] = [];
  for (const day of sorted) {
    const last = runs.at(-1);
    if (last && day === last.at(-1)! + 1) last.push(day);
    else runs.push([day]);
  }
  return runs
    .map((r) => (r.length > 1 ? `${DAYS[r[0]]}–${DAYS[r.at(-1)!]}` : DAYS[r[0]]))
    .join(", ");
}

/** A label/value pair in the inspector. */
function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-[13px] text-ink" title={value}>
        {value}
      </dd>
    </div>
  );
}

function PendingChange({
  change,
  agentId,
}: {
  change: ConfigChange;
  agentId: string;
}) {
  const tone =
    change.kind === "added"
      ? "text-success"
      : change.kind === "removed"
        ? "text-danger"
        : "text-muted-foreground";
  const body = (
    <>
      <span aria-hidden className={`font-mono text-[13px] leading-5 ${tone}`}>
        {CHANGE_MARK[change.kind]}
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-medium text-ink">
          {change.label}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
          {change.summary}
        </span>
      </span>
    </>
  );
  // A change we cannot place is still shown — it just does not pretend to link somewhere.
  return change.section ? (
    <Link
      href={`/studio/agents/${agentId}/${change.section}`}
      className="flex gap-2.5 rounded-lg px-2 py-2 no-underline transition hover:bg-muted"
    >
      {body}
    </Link>
  ) : (
    <div className="flex gap-2.5 px-2 py-2">{body}</div>
  );
}

export function AgentWorkspace({ agentId }: { agentId: string }) {
  const section = usePathname().split("/")[4] || "overview";
  const router = useRouter();
  const [agent, setAgent] = useState<StudioAgent | null>(null);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [calls, setCalls] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
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
    // Traffic for the inspector. Allowed to fail quietly — you came here to edit, and a
    // conversations query that is slow or empty must not hold the editor hostage.
    api
      .listConversations()
      .then((rows) => {
        if (!cancelled) setCalls(rows);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [agentId]);
  // Whether the workspace has a practice calendar connected and mapped, for the setup guide. Any
  // system the platform supports counts; which one is the practice's choice on Connections.
  const [calendarAccounts, setCalendarAccounts] = useState<number | null>(null);
  useEffect(() => {
    Promise.all([api.listConnections(), api.listCalendarSystems()])
      .then(([rows, systems]) =>
        setCalendarAccounts(
          rows.filter(
            (c) =>
              systems.some((system) => system.key === c.adapter) &&
              Object.keys(c.mapping ?? c.rooms ?? {}).length > 0,
          ).length,
        ),
      )
      .catch(() => setCalendarAccounts(null));
  }, []);
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
      setSavedAt(new Date());
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
  // Apply and save in one step, for actions that are a decision rather than typing: taking over
  // imported prices, adding a document. Saves the whole draft, so other edits are kept too.
  const commit = async (patch: Partial<AgentConfig>) => {
    if (!agent || !config) return;
    const next = { ...config, ...patch };
    setConfig(next);
    setBusy(true);
    setError("");
    try {
      adopt(await api.saveAgent(agent.id, agent.generation, next));
      setSavedAt(new Date());
      setNotice("Saved. Live calls are unchanged until you publish.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const live =
    agent?.history.find((r) => r.id === agent.published_instance_id) ?? null;
  const changes = useMemo(
    () => (config ? draftChanges(config, live?.config ?? null) : []),
    [config, live],
  );
  const metrics = useMemo(() => {
    if (!agent || !calls) return null;
    const ids = instanceIds(agent);
    return callMetrics(calls.filter((c) => ids.has(c.instance_id)));
  }, [agent, calls]);

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
  const title = SECTIONS.find(([key]) => key === section)?.[1] ?? "Overview";
  const share = metrics ? bookedShare(metrics) : null;
  const counts: Record<string, string | undefined> = {
    knowledge: (config.knowledge_ids ?? []).length
      ? String((config.knowledge_ids ?? []).length)
      : undefined,
    history: agent.history.length ? String(agent.history.length) : undefined,
  };

  return (
    <>
      <StudioTrail
        crumbs={[{ label: "Agents", href: "/studio" }, { label: config.name }]}
      />
      <StudioActions>
        <span
          aria-live="polite"
          className="mr-1 hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex"
        >
          {busy ? (
            "Saving…"
          ) : dirty ? (
            <>
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-warning" />
              Unsaved changes
            </>
          ) : (
            <>
              <span aria-hidden className="text-success">
                <Icon name="check" size={13} width={2.4} />
              </span>
              {savedAt
                ? `Saved ${savedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                : "Draft saved"}
            </>
          )}
        </span>
        <button
          className="studio-secondary"
          disabled={busy || !dirty}
          onClick={save}
        >
          Save
        </button>
        <button className="studio-secondary" disabled={busy} onClick={test}>
          <Icon name="phone" size={14} width={1.9} />
          <span className="hidden sm:inline">Test call</span>
        </button>
        <button
          className="studio-primary"
          disabled={busy || dirty}
          title={dirty ? "Save your draft first" : undefined}
          onClick={() => setReview(true)}
        >
          Publish
        </button>
      </StudioActions>

      <div className="lg:flex">
        {/* --- the agent tree ------------------------------------------------------- */}
        <nav
          aria-label="Agent sections"
          className="border-b border-border bg-surface px-3 py-2 lg:sticky lg:top-14 lg:h-[calc(100vh-3.5rem)] lg:w-[218px] lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:py-3.5"
        >
          <div className="flex gap-1 overflow-x-auto lg:block">
            {GROUPS.map(({ group, items }) => (
              <div key={group} className="contents lg:mb-1 lg:block">
                <p className="studio-eyebrow hidden px-2.5 pb-1.5 pt-2.5 lg:block">
                  {group}
                </p>
                {items.map(([key, label]) => {
                  const active = key === section;
                  return (
                    <Link
                      key={key}
                      href={`/studio/agents/${agentId}/${key}`}
                      aria-current={active ? "page" : undefined}
                      className={`studio-nav whitespace-nowrap no-underline lg:mb-0.5 ${active ? "studio-nav-on" : ""}`}
                    >
                      <span className="min-w-0 flex-1">{label}</span>
                      {counts[key] && (
                        <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                          {counts[key]}
                        </span>
                      )}
                      {key === "phone" && agent.channel?.active && (
                        <span
                          aria-label="Live"
                          className="h-1.5 w-1.5 shrink-0 rounded-full bg-success"
                        />
                      )}
                    </Link>
                  );
                })}
              </div>
            ))}
          </div>
        </nav>

        <div className="min-w-0 flex-1 xl:flex">
          {/* --- the editor --------------------------------------------------------- */}
          <main className="min-w-0 flex-1 px-5 py-7 sm:px-7">
            <div className="mx-auto max-w-[760px]">
              <div className="mb-6">
                <h1 className="text-xl font-semibold tracking-tight text-ink">
                  {title}
                </h1>
                <p className="mt-1.5 text-[13px] leading-6 text-muted-foreground">
                  {ABOUT[section]}
                </p>
              </div>
              {error && (
                <p
                  role="alert"
                  className="mb-5 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
                >
                  {error}
                </p>
              )}
              {notice && (
                <p
                  role="status"
                  className="mb-5 rounded-lg border border-success/20 bg-success/5 p-4 text-sm text-success"
                >
                  {notice}
                </p>
              )}
              {review && (
                <div className="mb-5">
                  <Section
                    title="Review publication"
                    description="This creates an immutable revision. Your live phone assignment is not changed."
                    collapsible={false}
                  >
                    <p className="text-sm text-ink">
                      Publishing <strong>{config.name}</strong> for{" "}
                      <strong>{config.practice_name || "your practice"}</strong>
                      , with {(config.knowledge_ids ?? []).length} document
                      {(config.knowledge_ids ?? []).length === 1 ? "" : "s"}.{" "}
                      {config.booking_enabled
                        ? "Booking enabled."
                        : "Booking disabled."}
                    </p>
                    {live && (
                      <div>
                        <p className="studio-eyebrow">
                          {changes.length} change
                          {changes.length === 1 ? "" : "s"} from revision{" "}
                          {live.revision}
                        </p>
                        <div className="mt-2 divide-y divide-border rounded-lg border border-border">
                          {changes.length === 0 && (
                            <p className="px-3 py-2.5 text-[13px] text-muted-foreground">
                              Identical to the published revision. Publishing
                              would create a revision that changes nothing.
                            </p>
                          )}
                          {changes.map((c) => (
                            <div
                              key={c.label}
                              className="flex gap-2.5 px-3 py-2.5"
                            >
                              <span
                                aria-hidden
                                className="font-mono text-[13px] text-muted-foreground"
                              >
                                {CHANGE_MARK[c.kind]}
                              </span>
                              <span className="text-[13px] text-ink">
                                {c.label}
                                <span className="ml-2 text-muted-foreground">
                                  {c.summary}
                                </span>
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
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
                      Publication checks configuration. It does not certify
                      audio quality or a real booking in your practice calendar.
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
                </div>
              )}
              <fieldset disabled={busy} className="min-w-0 space-y-5">
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
                      collapsible={false}
                    >
                      <div className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
                        {[
                          ["Agent", "Voice receptionist"],
                          [
                            "Published",
                            live ? `Revision ${live.revision}` : "Not yet",
                          ],
                          [
                            "Phone",
                            agent.channel?.active
                              ? "Answering calls"
                              : "Not answering",
                          ],
                        ].map(([label, value]) => (
                          <div key={label} className="bg-white px-4 py-3.5">
                            <p className="text-xs text-muted-foreground">
                              {label}
                            </p>
                            <p className="mt-1.5 text-sm font-medium text-ink">
                              {value}
                            </p>
                          </div>
                        ))}
                      </div>
                    </Section>
                    <Section
                      title="Set up, step by step"
                      description="Each step opens the place where you do it. The next one is highlighted."
                      collapsible={false}
                    >
                      {(() => {
                        // Generic on purpose: the practice connects whichever system its
                        // calendar lives in, chosen on the Connections page, not here.
                        const steps: {
                          label: string;
                          desc: string;
                          done: boolean;
                          optional?: boolean;
                          actions: { label: string; href?: string; onClick?: () => void; disabled?: boolean }[];
                        }[] = [
                          {
                            label: "Practice details",
                            desc: "Name, address, phone and opening hours.",
                            done:
                              !!config.practice_name &&
                              !!config.street &&
                              !!config.city &&
                              !!config.phone,
                            actions: [{ label: "Open Knowledge base", href: `/studio/agents/${agent.id}/knowledge` }],
                          },
                          {
                            label: "Documents the agent may answer from",
                            desc: "Your services, prices, FAQ or a page of your website — added and ticked in the Knowledge base, under Documents.",
                            done: (config.knowledge_ids ?? []).length > 0,
                            optional: true,
                            actions: [{ label: "Add documents", href: `/studio/agents/${agent.id}/knowledge` }],
                          },
                          {
                            label: "Greeting and conversation style",
                            desc: "How the agent greets callers and how it should speak.",
                            done: !!config.greeting,
                            actions: [{ label: "Open Instructions", href: `/studio/agents/${agent.id}/instructions` }],
                          },
                          {
                            label: "Privacy policy",
                            desc: "The reference recorded when a patient card is created. Required while booking is on.",
                            done: !!config.consent_policy_id || !config.booking_enabled,
                            actions: [{ label: "Open Capabilities", href: `/studio/agents/${agent.id}/capabilities` }],
                          },
                          {
                            label: "Connect your practice calendar",
                            desc: "Connect the system your appointments live in and map your calendars. Then choose it in Phone & handoff and run the check.",
                            done: (calendarAccounts ?? 0) > 0,
                            actions: [
                              { label: "Open Connections", href: "/studio/governance/connections" },
                              { label: "Choose it for this agent", href: `/studio/agents/${agent.id}/phone` },
                            ],
                          },
                          {
                            label: "Staff notifications",
                            desc: "Who receives call summaries and callback requests by email.",
                            done: (config.recipients ?? []).length > 0,
                            actions: [{ label: "Open Phone & handoff", href: `/studio/agents/${agent.id}/phone` }],
                          },
                          {
                            label: "Test call",
                            desc: "Talk to the agent in your browser — with a sandbox, or on your real calendar, read-only or writing test appointments.",
                            done: false,
                            optional: true,
                            actions: [{ label: "Start a test call", onClick: () => void test() }],
                          },
                          {
                            label: "Publish",
                            desc: "Makes this draft the version a phone number can answer with.",
                            done: !!agent.published_instance_id,
                            actions: [
                              {
                                label: dirty ? "Save, then publish" : "Publish",
                                onClick: () => setReview(true),
                                disabled: dirty,
                              },
                            ],
                          },
                          {
                            label: "Connect your phone number",
                            desc: "Choose a number and activate. Callers dialling it reach this agent.",
                            done: !!agent.channel?.active,
                            actions: [{ label: "Open Phone & handoff", href: `/studio/agents/${agent.id}/phone` }],
                          },
                        ];
                        const next = steps.findIndex((step) => !step.done && !step.optional);
                        return steps.map((step, index) => (
                          <div
                            key={step.label}
                            className={`flex flex-col gap-3 border-b border-border pb-4 last:border-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between ${index === next ? "-mx-3 rounded-lg bg-accent/60 px-3 pt-3" : ""}`}
                          >
                            <div className="flex min-w-0 items-start gap-3">
                              <span
                                className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold ${step.done ? "bg-success/10 text-success" : "bg-muted text-muted-foreground"}`}
                              >
                                {step.done ? <Icon name="check" size={13} width={2.2} /> : index + 1}
                              </span>
                              <div className="min-w-0">
                                <p className="text-sm font-medium text-ink">
                                  {step.label}
                                  {step.optional && (
                                    <span className="ml-2 text-xs font-normal text-muted-foreground">optional</span>
                                  )}
                                  {index === next && (
                                    <span className="ml-2 text-xs font-semibold text-primary">Next</span>
                                  )}
                                </p>
                                <p className="mt-1 text-sm leading-6 text-muted-foreground">{step.desc}</p>
                              </div>
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2 pl-9 sm:pl-0">
                              {step.actions.map((action) =>
                                action.href ? (
                                  <Link
                                    key={action.label}
                                    href={action.href}
                                    className={`${index === next ? "studio-primary" : "studio-secondary"} whitespace-nowrap no-underline`}
                                  >
                                    {action.label}
                                  </Link>
                                ) : (
                                  <button
                                    key={action.label}
                                    type="button"
                                    disabled={busy || action.disabled}
                                    onClick={action.onClick}
                                    className={`${index === next ? "studio-primary" : "studio-secondary"} whitespace-nowrap`}
                                  >
                                    {action.label}
                                  </button>
                                ),
                              )}
                            </div>
                          </div>
                        ));
                      })()}
                    </Section>
                  </>
                )}
                <ConfigEditor
                  section={section}
                  config={config}
                  change={change}
                  commit={commit}
                  systems={agent.systems}
                />
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
                {["instructions", "knowledge", "capabilities", "voice", "phone"].includes(section) && (
                  // Save where you edit: the top bar's Save is a screen away from the field you
                  // just typed in. Sticky while there is something to save.
                  <div
                    className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-white px-4 py-3 ${dirty ? "sticky bottom-3 z-10 shadow-sm" : ""}`}
                  >
                    <span className={`text-sm ${dirty ? "font-medium text-ink" : "text-muted-foreground"}`}>
                      {dirty ? "You have unsaved changes." : "All changes saved."}
                    </span>
                    <button
                      type="button"
                      className="studio-primary"
                      disabled={busy || !dirty}
                      onClick={() => void save()}
                    >
                      {busy ? "Saving…" : "Save changes"}
                    </button>
                  </div>
                )}
                {section === "tests" &&
                  (dirty ? (
                    <Section
                      title="Save before testing"
                      description="A rehearsal uses the saved draft so its results refer to an exact configuration."
                      collapsible={false}
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
                    meta={`${agent.history.length} revision${agent.history.length === 1 ? "" : "s"}`}
                    collapsible={false}
                  >
                    {!agent.history.length && (
                      <p className="text-sm text-muted-foreground">
                        No published revisions yet. Your draft is saved
                        separately.
                      </p>
                    )}
                    {agent.history.map((r) => (
                      <div
                        className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4 last:border-0 last:pb-0"
                        key={r.id}
                      >
                        <div>
                          <p className="text-sm font-medium text-ink">
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

          {/* --- the inspector ------------------------------------------------------ */}
          <aside
            aria-label="Agent summary"
            className="border-t border-border bg-surface px-5 py-6 xl:sticky xl:top-14 xl:h-[calc(100vh-3.5rem)] xl:w-[284px] xl:shrink-0 xl:overflow-y-auto xl:border-l xl:border-t-0 xl:px-5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-ink">
                  {config.name}
                </p>
                <p className="mt-0.5 font-mono text-[11px] text-faint">
                  {agent.id.slice(0, 14)}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${agent.channel?.active ? "border-success/25 bg-success/10 text-success" : "border-border bg-muted text-muted-foreground"}`}
              >
                {agent.channel?.active
                  ? "Live"
                  : agent.published_instance_id
                    ? "Published"
                    : "Draft"}
              </span>
            </div>

            <dl className="mt-5 divide-y divide-border border-y border-border">
              <Detail
                label="Voice"
                value={config.voice_id || "Studio default"}
              />
              <Detail label="Language" value={LANGUAGE[config.locale]} />
              <Detail
                label="Number"
                value={agent.channel?.phone_number ?? "Not connected"}
              />
              <Detail
                label="Days"
                value={openingDays(config.weekdays)}
              />
              <Detail
                label="Hours"
                value={`${config.open_from || "—"}–${config.open_until || "—"}`}
              />
            </dl>

            <p className="studio-eyebrow mt-6">Last 7 days</p>
            {metrics === null ? (
              <p className="mt-2 text-[13px] text-muted-foreground">
                No call records available.
              </p>
            ) : (
              <div className="mt-3 flex gap-7">
                <div>
                  <p className="studio-stat">{metrics.calls}</p>
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    calls handled
                  </p>
                </div>
                <div>
                  <p className="studio-stat">
                    {share === null ? "—" : `${share}%`}
                  </p>
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    booked in call
                  </p>
                </div>
              </div>
            )}

            <div className="mt-6 flex items-baseline justify-between gap-2">
              <p className="studio-eyebrow">Pending changes</p>
              {live && (
                <span className="text-[11px] text-faint">
                  vs. revision {live.revision}
                </span>
              )}
            </div>
            {!live ? (
              <p className="mt-2 text-[13px] leading-6 text-muted-foreground">
                Never published. Nothing is answering this number yet, so there
                is nothing to compare your draft against.
              </p>
            ) : changes.length === 0 ? (
              <p className="mt-2 text-[13px] leading-6 text-muted-foreground">
                Your draft matches the published revision.
              </p>
            ) : (
              <>
                <div className="-mx-2 mt-1.5 space-y-0.5">
                  {changes.map((c) => (
                    <PendingChange
                      key={c.label}
                      change={c}
                      agentId={agent.id}
                    />
                  ))}
                </div>
                <button
                  className="studio-secondary mt-3 w-full"
                  disabled={busy || dirty}
                  title={dirty ? "Save your draft first" : undefined}
                  onClick={() => setReview(true)}
                >
                  Review diff
                </button>
              </>
            )}
          </aside>
        </div>
      </div>
    </>
  );
}
