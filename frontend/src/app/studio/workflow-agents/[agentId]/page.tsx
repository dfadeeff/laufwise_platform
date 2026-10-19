"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import { SchemaForm } from "@/features/workflows/SchemaForm";
import { CaseStatusChip } from "@/features/workflows/CaseStatusChip";
import type { WorkflowAgentDetail } from "@/types";

// A workflow agent (ADR-0030): the standing decisions it computes with, and its cases. Each case
// is one piece of work: its documents, the input read from them, and the result.

export default function WorkflowAgentPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = use(params);
  const router = useRouter();
  const [agent, setAgent] = useState<WorkflowAgentDetail | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    setError("");
    api
      .getWorkflowAgent(agentId)
      .then((a) => {
        setAgent(a);
        setSettings(a.settings);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, [agentId]);

  const dirty = agent !== null && JSON.stringify(settings) !== JSON.stringify(agent.settings);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.saveWorkflowAgentSettings(agentId, settings);
      setAgent(agent && { ...agent, settings: res.settings });
      setSaved("Saved. Cases use these settings the next time they are computed.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  const newCase = async () => {
    setBusy(true);
    setError("");
    try {
      const c = await api.createCase(agentId, title.trim());
      router.push(`/studio/cases/${c.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail
        crumbs={[{ label: "Agents", href: "/studio" }, { label: agent?.name ?? "Workflow agent" }]}
      />
      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
        >
          {error}
        </div>
      )}
      {!agent && !error && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading…
        </p>
      )}
      {agent && (
        <>
          <p className="studio-eyebrow text-primary">
            Workflow agent · {agent.workflow.label}
          </p>
          <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">
            {agent.name}
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {agent.workflow.summary}
          </p>

          <section className="mt-10">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-ink">Cases</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  One case per piece of work: its documents, the input read from
                  them, and the result computed from that input.
                </p>
              </div>
            </div>
            <form
              className="mt-4 flex flex-col gap-2 sm:flex-row"
              onSubmit={(e) => {
                e.preventDefault();
                if (title.trim()) newCase();
              }}
            >
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Lindenstraße 14, Wohnung 3 · 2025"
                aria-label="Title of the new case"
                className="h-9 w-full rounded-lg border border-border bg-white px-3 text-[13px] text-ink outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 sm:max-w-md"
              />
              <button className="studio-primary" disabled={busy || !title.trim()}>
                <Icon name="plus" size={15} width={2.2} />
                New case
              </button>
            </form>
            {agent.cases.length === 0 ? (
              <p className="mt-4 text-sm text-muted-foreground">No cases yet.</p>
            ) : (
              <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-white">
                <table className="w-full min-w-[520px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      {["Case", "Status", "Updated"].map((h) => (
                        <th key={h} scope="col" className="studio-eyebrow px-4 py-2.5 font-semibold">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {agent.cases.map((c) => (
                      <tr
                        key={c.id}
                        onClick={() => router.push(`/studio/cases/${c.id}`)}
                        className="cursor-pointer border-b border-border last:border-0 hover:bg-surface"
                      >
                        <td className="px-4 py-3">
                          <Link
                            href={`/studio/cases/${c.id}`}
                            className="font-medium text-ink no-underline hover:text-primary"
                          >
                            {c.title}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <CaseStatusChip status={c.status} />
                        </td>
                        <td className="px-4 py-3 text-[13px] text-muted-foreground">
                          {c.updated_at ? new Date(c.updated_at).toLocaleString() : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {agent.settings_schema && (
            <section className="studio-section mt-10 p-5 sm:p-6">
              <h2 className="text-lg font-semibold text-ink">Settings</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Standing decisions this agent applies to every case.
              </p>
              <div className="mt-5">
                <SchemaForm
                  schema={agent.settings_schema}
                  value={settings}
                  onChange={(v) => {
                    setSettings(v);
                    setSaved("");
                  }}
                />
              </div>
              <div className="mt-5 flex items-center gap-3">
                <button className="studio-primary" disabled={!dirty || busy} onClick={save}>
                  Save settings
                </button>
                {saved && <span className="text-sm text-muted-foreground">{saved}</span>}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}
