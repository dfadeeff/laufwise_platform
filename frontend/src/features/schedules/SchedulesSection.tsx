"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import type { Schedulability, Schedule, ScheduleOccurrence, ScheduleTargetKind } from "@/types";
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

function History({ schedule, onRefresh }: { schedule: Schedule; onRefresh: () => void }) {
  const [rows, setRows] = useState<ScheduleOccurrence[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.listScheduleOccurrences(schedule.id).then(setRows).catch((e) => setError(message(e)));
  }, [schedule.id, schedule.last?.id, schedule.last?.status]);
  const refresh = (
    <button type="button" className="mt-2 text-[13px] font-medium text-primary" onClick={onRefresh}>
      Refresh
    </button>
  );
  if (error) return <p role="alert" className="text-[13px] text-danger">{error}</p>;
  if (!rows) return <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>;
  if (rows.length === 0) return <div><p className="text-[13px] text-muted-foreground">It has not run yet.</p>{refresh}</div>;
  return (
    <div>
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
                  ) : o.run_ref?.startsWith("import_job:") ? (
                    <Link href="/studio/history" className="text-primary">
                      An import was started{o.error ? ` · ${o.error}` : ""}
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
    {refresh}
    </div>
  );
}

function ScheduleCard({
  schedule,
  schedulability,
  earlier = false,
  onChanged,
  onDeleted,
}: {
  earlier?: boolean;
  schedule: Schedule;
  schedulability?: Schedulability | null;
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
          schema={schedule.input_schema ?? schedulability?.input_schema ?? null}
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
          {earlier && (
            <p className="mt-0.5 text-[12px] text-warning">
              Runs the settings saved when it was created, not the ones on this page now.
            </p>
          )}
          <p className="mt-0.5 text-[12px] text-muted-foreground">
            {schedule.enabled
              ? `Next: ${when(schedule.next_run_at, schedule.timezone)} (${schedule.timezone})`
              : "Not running until resumed"}
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
                onChanged(await api.getSchedule(schedule.id));
                setNotice("Queued: it runs within a minute. Refresh its history to see the result.");
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
          <History
            schedule={schedule}
            onRefresh={() => act(async () => onChanged(await api.getSchedule(schedule.id)))}
          />
        </div>
      )}
    </li>
  );
}

/** Says so when the platform's schedule worker is not running: schedules are kept, and nothing
 *  fires them until it runs. */
export function WorkerStatus() {
  const [running, setRunning] = useState<boolean | null>(null);
  useEffect(() => {
    api.schedulerStatus().then((s) => setRunning(s.running)).catch(() => setRunning(null));
  }, []);
  if (running !== false) return null;
  return (
    <p role="status" className="mt-4 rounded-lg border border-warning/40 bg-warning/5 p-3 text-[13px] text-ink">
      The platform&apos;s scheduler is not running yet. Schedules are saved, but nothing runs them until it does.
    </p>
  );
}

export function SchedulesSection({
  kind,
  targetId,
  schedulability: given,
  ensureTarget,
  group,
  intro,
}: {
  kind: ScheduleTargetKind;
  /** What a new schedule runs; null until there is one (`ensureTarget` makes it). */
  targetId: string | null;
  schedulability?: Schedulability;
  /** Puts the settings on screen into effect and returns what a new schedule runs. */
  ensureTarget?: () => Promise<string>;
  /** List every schedule of this kind and key (a runbook's earlier deployments too), not only
   *  the current target's. */
  group?: string;
  intro: string;
}) {
  const [current, setCurrent] = useState<string | null>(targetId);
  const [able, setAble] = useState<Schedulability | null>(given ?? null);
  const [schedules, setSchedules] = useState<Schedule[] | null>(targetId || group ? null : []);
  const list = (id: string | null) =>
    group
      ? api.listSchedules().then((all) => all.filter((s) => s.target.kind === kind && s.target.key === group))
      : id
        ? api.listSchedules(id)
        : Promise.resolve([] as Schedule[]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = (id: string | null) => {
    list(id).then(setSchedules).catch((e) => setError(message(e)));
    if (id && (!given || id !== targetId)) api.getSchedulability(kind, id).then(setAble).catch((e) => setError(message(e)));
  };
  useEffect(() => {
    setCurrent(targetId);
    if (given) setAble(given);
    load(targetId);
  }, [targetId, kind]); // what it is for, not the callbacks

  const begin = async () => {
    setBusy(true);
    setError("");
    try {
      const id = ensureTarget ? await ensureTarget() : current;
      if (!id) return;
      const next = given && id === targetId ? given : await api.getSchedulability(kind, id);
      if (id !== current) {
        setCurrent(id);
        setSchedules(await list(id));
      }
      setAble(next);
      if (next.schedulable) setAdding(true);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const create = async (d: ScheduleDraft) => {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      const made = await api.createSchedule({ target: { kind, id: current }, enabled: true, ...d });
      setSchedules((all) => [...(all ?? []), made]);
      setAdding(false);
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  };

  const moveOff = async () => {
    if (!current) return;
    setBusy(true);
    setError("");
    try {
      const made = await api.moveOffOldClock(current);
      setSchedules((all) => [...(all ?? []), ...made]);
      setAble(await api.getSchedulability(kind, current));
    } catch (e) {
      setError(message(e));
    }
    setBusy(false);
  };

  const offerNew = !adding && !able?.old_clock && (ensureTarget ? true : Boolean(able?.schedulable));

  return (
    <section className="studio-section mt-10 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-ink">Schedules</h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{intro}</p>
        </div>
        {offerNew && (
          <button className="studio-primary shrink-0" disabled={busy} onClick={begin}>
            <Icon name="plus" size={15} width={2.2} />
            New schedule
          </button>
        )}
      </div>

      <WorkerStatus />
      {able && !able.schedulable && (
        <div className="mt-4 rounded-lg border border-border bg-muted/50 p-3 text-[13px] text-ink">
          {able.old_clock ? (
            <>
              <p>
                This runs on the old clock (&ldquo;Keep the website in sync&rdquo;: the next 7 days every 20
                minutes, the rest of the horizon overnight). Move it to the new scheduler to choose your own
                times. It becomes two schedules doing about the same: the next 8 days every 30 minutes, and the
                whole horizon (70 days) at 03:00. It moves in one step, so it never runs on both. This needs the
                platform&apos;s scheduler running.
              </p>
              <button className="studio-secondary mt-3" disabled={busy} onClick={moveOff}>
                Move to the new scheduler
              </button>
            </>
          ) : kind === "workflow" ? (
            <p>
              This agent cannot run on a schedule yet: {able.reason}. A workflow you own can allow it: open it under
              Workflows, switch on <em>Can run on a schedule</em>, and publish.
            </p>
          ) : (
            <p>This cannot run on a schedule: {able.reason}.</p>
          )}
        </div>
      )}
      {error && <p role="alert" className="mt-4 text-sm text-danger">{error}</p>}

      {adding && able && (
        <div className="mt-5 rounded-xl border border-border bg-white p-4 sm:p-5">
          <ScheduleEditor schema={able.input_schema} initial={null} busy={busy} onSave={create} onCancel={() => setAdding(false)} />
        </div>
      )}

      {schedules === null && !error && <p role="status" className="mt-4 text-sm text-muted-foreground">Loading…</p>}
      {schedules && schedules.length === 0 && !adding && (
        <p className="mt-4 text-sm text-muted-foreground">No schedules yet. It runs only when someone starts it.</p>
      )}
      {schedules && schedules.length > 0 && (
        <ul className="mt-5 space-y-3">
          {schedules.map((s) => (
            <ScheduleCard
              key={s.id}
              earlier={Boolean(group && current && s.target.id !== current)}
              schedule={s}
              schedulability={able}
              onChanged={(n) => setSchedules((all) => (all ?? []).map((x) => (x.id === n.id ? n : x)))}
              onDeleted={() => setSchedules((all) => (all ?? []).filter((x) => x.id !== s.id))}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
