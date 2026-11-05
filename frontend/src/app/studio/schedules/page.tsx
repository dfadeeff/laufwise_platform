"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import { OccurrenceChip } from "@/features/schedules/SchedulesSection";
import { describeRule, when } from "@/features/schedules/rules";
import type { Schedule } from "@/types";

// Every schedule of the workspace in one place, soonest first (ADR-0040 D8): what runs next,
// what ran last, and one click to stop any of it. Changing a schedule happens on its agent.

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    api.listSchedules().then(setSchedules).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const toggle = async (s: Schedule) => {
    setBusy(s.id);
    setError("");
    try {
      const next = s.enabled ? await api.pauseSchedule(s.id) : await api.resumeSchedule(s.id);
      setSchedules((all) =>
        (all ?? [])
          .map((x) => (x.id === next.id ? next : x))
          .sort((a, b) => (a.next_run_at ?? "~").localeCompare(b.next_run_at ?? "~") || a.name.localeCompare(b.name)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy("");
  };

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Schedules" }]} />
      <p className="studio-eyebrow text-primary">Workspace</p>
      <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">Schedules</h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        Everything this workspace&apos;s agents do on their own, soonest first. The platform runs them; pausing one
        stops it at once. To change one, open its agent.
      </p>

      {error && (
        <div role="alert" className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger">
          {error}
        </div>
      )}
      {schedules === null && !error && (
        <p role="status" className="mt-6 text-sm text-muted-foreground">Loading…</p>
      )}
      {schedules && schedules.length === 0 && (
        <p className="mt-6 text-sm text-muted-foreground">
          No schedules yet. Open a workflow agent under <Link href="/studio">Agents</Link> and add one there.
        </p>
      )}
      {schedules && schedules.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-xl border border-border bg-white">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-border">
                {["Schedule", "Agent", "When", "Next run", "Last run", ""].map((h) => (
                  <th key={h} scope="col" className="studio-eyebrow px-4 py-2.5 font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-b border-border align-top last:border-0">
                  <td className="px-4 py-3 font-medium text-ink">{s.name}</td>
                  <td className="px-4 py-3">
                    <Link href={`/studio/workflow-agents/${s.target.id}`} className="text-ink no-underline hover:text-primary">
                      {s.target.name ?? "—"}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-[13px] text-ink">{describeRule(s.rule)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-[13px] text-ink">
                    {s.enabled ? (
                      <>
                        {when(s.next_run_at, s.timezone)}
                        <span className="block text-[11px] text-muted-foreground">{s.timezone}</span>
                      </>
                    ) : (
                      <span className="text-muted-foreground">Paused</span>
                    )}
                  </td>
                  <td className="px-4 py-3">{s.last ? <OccurrenceChip status={s.last.status} /> : <span className="text-[13px] text-muted-foreground">—</span>}</td>
                  <td className="px-4 py-3 text-right">
                    <button className="studio-secondary" disabled={busy === s.id} onClick={() => toggle(s)}>
                      {s.enabled ? "Pause" : "Resume"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
