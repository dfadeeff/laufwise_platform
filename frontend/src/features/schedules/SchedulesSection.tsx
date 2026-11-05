"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import type { Schedulability, Schedule, ScheduleOccurrence } from "@/types";
import { ScheduleEditor, type ScheduleDraft } from "./ScheduleEditor";
import { OCCURRENCE, caseOf, describeRule, when } from "./rules";

// An agent's schedules (ADR-0040 D8): when it runs on its own, whether that is on, and what came
// of each run. The platform's own worker runs them; nothing here touches the hosting.

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function OccurrenceChip({ status }: { status: ScheduleOccurrence["status"] }) {
  const s = OCCURRENCE[status] ?? OCCURRENCE.queued;
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${s.chip}`}>
      {s.label}
    </span>
  );
}

function History({ schedule }: { schedule: Schedule }) {
  const [rows, setRows] = useState<ScheduleOccurrence[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.listScheduleOccurrences(schedule.id).then(setRows).catch((e) => setError(message(e)));
  }, [schedule.id, schedule.last?.id, schedule.last?.status]);
  if (error) return <p role="alert" className="text-[13px] text-danger">{error}</p>;
  if (!rows) return <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>;
  if (rows.length === 0) return <p className="text-[13px] text-muted-foreground">It has not run yet.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-white">
      <table className="w-full min-w-[520px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-border">
            {["Due", "", "What came of it"].map((h) => (
              <th key={h} scope="col" className="studio-eyebrow px-3 py-2 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => {
            const caseId = caseOf(o.run_ref);
            return (
              <tr key={o.id} className="border-b border-border align-top last:border-0">
                <td className="whitespace-nowrap px-3 py-2 text-ink">
                  {when(o.scheduled_for, schedule.timezone)}
                  {o.origin === "run_now" && <span className="ml-1.5 text-[11px] text-muted-foreground">run now</span>}
                </td>
                <td className="px-3 py-2"><OccurrenceChip status={o.status} /></td>
                <td className="px-3 py-2 text-muted-foreground">
                  {caseId ? (
                    <Link href={`/studio/cases/${caseId}`} className="text-primary">
                      Open the case{o.outcome ? ` (${o.outcome})` : ""}
                    </Link>
                  ) : (
                    o.error || "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ScheduleCard({
  schedule,
  schedulability,
  onChanged,
  onDeleted,
}: {
  schedule: Schedule;
  schedulability: Schedulability;
  onChanged: (s: Schedule) => void;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  };

  if (editing)
    return (
      <li className="rounded-xl border border-border bg-white p-4 sm:p-5">
        {error && <p role="alert" className="mb-4 text-sm text-danger">{error}</p>}
        <ScheduleEditor
          schema={schedulability.input_schema}
          initial={schedule}
          busy={busy}
          onCancel={() => setEditing(false)}
          onSave={(d: ScheduleDraft) =>
            act(async () => {
              onChanged(await api.changeSchedule(schedule.id, schedule.revision, d));
              setEditing(false);
            })
          }
        />
      </li>
    );

  return (
    <li className="rounded-xl border border-border bg-white p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
            <Icon name="clock" size={15} />
            {schedule.name}
            <span
              className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                schedule.enabled ? "border-success/25 bg-success/10 text-success" : "border-border bg-muted text-muted-foreground"
              }`}
            >
              {schedule.enabled ? "On" : "Paused"}
            </span>
          </p>
          <p className="mt-1 text-[13px] text-ink">{describeRule(schedule.rule)}</p>
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            {schedule.enabled ? `Next: ${when(schedule.next_run_at, schedule.timezone)}` : "Not running until resumed"}
            {" · "}runs version {schedule.version?.number ?? "?"}
            {schedule.last && (
              <>
                {" · "}last: <OccurrenceChip status={schedule.last.status} />
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {schedule.enabled ? (
            <button className="studio-secondary" disabled={busy} onClick={() => act(async () => onChanged(await api.pauseSchedule(schedule.id)))}>
              Pause
            </button>
          ) : (
            <button className="studio-primary" disabled={busy} onClick={() => act(async () => onChanged(await api.resumeSchedule(schedule.id)))}>
              Resume
            </button>
          )}
          <button
            className="studio-secondary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                await api.runScheduleNow(schedule.id);
                setNotice("Queued: it runs within a minute. The result shows in its history.");
                setOpen(true);
              })
            }
          >
            Run now
          </button>
          <button className="studio-secondary" disabled={busy} onClick={() => setEditing(true)}>
            Edit
          </button>
          <button
            className="studio-secondary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                if (!window.confirm(`Delete "${schedule.name}"? Its history is kept.`)) return;
                await api.deleteSchedule(schedule.id);
                onDeleted();
              })
            }
          >
            Delete
          </button>
        </div>
      </div>

      {schedule.newer_version && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-[13px] text-ink sm:flex-row sm:items-center sm:justify-between">
          <span>
            Version {schedule.newer_version.number} is published. This schedule keeps running version{" "}
            {schedule.version?.number} until you move it.
          </span>
          <button
            className="studio-secondary"
            disabled={busy}
            onClick={() =>
              act(async () => onChanged(await api.changeSchedule(schedule.id, schedule.revision, { version_id: schedule.newer_version!.id })))
            }
          >
            Run version {schedule.newer_version.number}
          </button>
        </div>
      )}

      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
      {notice && <p role="status" className="mt-3 text-sm text-muted-foreground">{notice}</p>}

      <button type="button" className="mt-3 text-[13px] font-medium text-primary" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide history" : "Show history"}
      </button>
      {open && (
        <div className="mt-2">
          <History schedule={schedule} />
        </div>
      )}
    </li>
  );
}

export function SchedulesSection({ agentId, schedulability }: { agentId: string; schedulability: Schedulability }) {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    api.listSchedules(agentId).then(setSchedules).catch((e) => setError(message(e)));
  };
  useEffect(load, [agentId]);

  const create = async (d: ScheduleDraft) => {
    setBusy(true);
    setError("");
    try {
      const made = await api.createSchedule({ target: { kind: "workflow", id: agentId }, enabled: true, ...d });
      setSchedules([...(schedules ?? []), made]);
      setAdding(false);
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  };

  return (
    <section className="studio-section mt-10 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ink">Schedules</h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            When this agent runs on its own. Each run is a new case, computed and checked like one you start;
            anything it would send waits for a person&apos;s approval.
          </p>
        </div>
        {schedulability.schedulable && !adding && (
          <button className="studio-primary shrink-0" onClick={() => setAdding(true)}>
            <Icon name="plus" size={15} width={2.2} />
            New schedule
          </button>
        )}
      </div>

      {!schedulability.schedulable && (
        <p className="mt-4 rounded-lg border border-border bg-muted/50 p-3 text-[13px] text-ink">
          This agent cannot run on a schedule yet: {schedulability.reason}. A workflow you own can allow it:
          open it under Workflows, switch on <em>Can run on a schedule</em>, and publish.
        </p>
      )}
      {error && <p role="alert" className="mt-4 text-sm text-danger">{error}</p>}

      {adding && (
        <div className="mt-5 rounded-xl border border-border bg-white p-4 sm:p-5">
          <ScheduleEditor schema={schedulability.input_schema} initial={null} busy={busy} onSave={create} onCancel={() => setAdding(false)} />
        </div>
      )}

      {schedules === null && !error && <p role="status" className="mt-4 text-sm text-muted-foreground">Loading…</p>}
      {schedules && schedules.length === 0 && !adding && schedulability.schedulable && (
        <p className="mt-4 text-sm text-muted-foreground">No schedules yet. It runs only when someone starts a case.</p>
      )}
      {schedules && schedules.length > 0 && (
        <ul className="mt-5 space-y-3">
          {schedules.map((s) => (
            <ScheduleCard
              key={s.id}
              schedule={s}
              schedulability={schedulability}
              onChanged={(n) => setSchedules(schedules.map((x) => (x.id === n.id ? n : x)))}
              onDeleted={() => setSchedules(schedules.filter((x) => x.id !== s.id))}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
