"use client";

import { useState } from "react";
import Link from "next/link";
import { SAMPLE_RUNS } from "@/lib/sample-runs";
import { STATUS } from "@/lib/status";
import type { ConsoleRun, ConsoleStep, StepStatus } from "@/types";

// Operator console — master-detail Runs view. Renders the enforced loop per step
// (OK / BLOCKED / REJECTED). Data is the sample fixture until the engine is wired.

function StatusBadge({ status }: { status: StepStatus }) {
  const s = STATUS[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${s.chip}`}>
      <span aria-hidden>{s.glyph}</span>
      {s.label}
    </span>
  );
}

function ConsoleHeader() {
  const tabs = [
    { label: "Runs", active: true },
    { label: "Approvals", active: false },
    { label: "Runbooks", active: false },
  ];
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
        <div className="flex items-center gap-8">
          <Link href="/" className="flex items-center gap-2">
            <span className="inline-block h-5 w-5 rounded-[5px] bg-primary" />
            <span className="font-display text-lg tracking-tight text-ink">Laufwise</span>
          </Link>
          <nav className="hidden items-center gap-5 text-sm md:flex">
            {tabs.map((t) => (
              <span
                key={t.label}
                className={t.active ? "font-medium text-ink" : "text-muted-foreground"}
              >
                {t.label}
              </span>
            ))}
          </nav>
        </div>
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          Console
        </span>
      </div>
    </header>
  );
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: ConsoleRun;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`flex w-full items-center gap-3 border-b border-border px-4 py-3 text-left last:border-b-0 hover:bg-muted/60 ${
        selected ? "bg-muted" : ""
      }`}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS[run.status].dot}`} aria-hidden />
      <span className="w-24 shrink-0 font-mono text-xs text-muted-foreground">{run.id}</span>
      <span className="flex-1 truncate text-sm text-ink">{run.runbook}</span>
      <span className="hidden shrink-0 font-mono text-[11px] text-muted-foreground sm:inline">
        {run.duration}
      </span>
      <StatusBadge status={run.status} />
    </button>
  );
}

function StepItem({ step }: { step: ConsoleStep }) {
  const s = STATUS[step.status];
  const flagged = step.status === "blocked" || step.status === "rejected";
  return (
    <li className="relative pl-7">
      <span
        className={`absolute left-0 top-1 flex h-5 w-5 items-center justify-center rounded-full text-[10px] text-background ${s.dot}`}
        aria-hidden
      >
        {s.glyph}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{step.id}</span>
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          {step.phase}
        </span>
        {step.approved && (
          <span className="rounded-md border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[11px] text-primary">
            approved
          </span>
        )}
      </div>
      {flagged && (
        <div
          className={`mt-2 rounded-lg border px-3 py-2 text-sm ${
            step.status === "blocked"
              ? "border-warning/20 bg-warning/5 text-foreground"
              : "border-danger/20 bg-danger/5 text-foreground"
          }`}
        >
          <span className="font-medium">
            {step.status === "blocked" ? "Blocked: " : "Rejected: "}
          </span>
          {step.reason}
          {step.blockedTool && (
            <div className="mt-1 font-mono text-[11px] text-muted-foreground">
              blocked tool: {step.blockedTool}
            </div>
          )}
          {step.note && (
            <div className="mt-1 font-mono text-[11px] text-muted-foreground">
              note: {step.note}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function RunDetail({ run }: { run: ConsoleRun }) {
  return (
    <div className="rounded-xl border border-border bg-surface">
      <div className="border-b border-border p-5">
        <div className="flex items-center justify-between">
          <span className="font-mono text-sm text-ink">{run.id}</span>
          <StatusBadge status={run.status} />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <Meta label="Runbook" value={run.runbook} />
          <Meta label="Started" value={run.started} />
          <Meta label="Duration" value={run.duration} />
          <Meta label="Steps" value={String(run.steps.length)} />
        </div>
      </div>
      <div className="p-5">
        <div className="mb-4 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          Enforced loop
        </div>
        <ol className="space-y-5 border-l border-border pl-2">
          {run.steps.map((step, i) => (
            <StepItem key={`${step.id}-${i}`} step={step} />
          ))}
        </ol>
      </div>
      <div className="border-t border-border px-5 py-3 font-mono text-[11px] text-muted-foreground">
        trace: {run.trace}
      </div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 truncate text-ink">{value}</div>
    </div>
  );
}

export default function RunsPage() {
  const runs = SAMPLE_RUNS;
  const [selectedId, setSelectedId] = useState(runs[0].id);
  const selected = runs.find((r) => r.id === selectedId) ?? runs[0];

  const counts = runs.reduce(
    (acc, r) => ((acc[r.status] = (acc[r.status] ?? 0) + 1), acc),
    {} as Record<StepStatus, number>,
  );

  return (
    <div className="min-h-screen bg-background">
      <ConsoleHeader />
      <main className="mx-auto max-w-7xl px-5 py-8 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">Runs</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Every run, enforced step by step against the system of record.
            </p>
          </div>
          <span className="rounded-md border border-warning/20 bg-warning/10 px-2 py-1 font-mono text-[11px] text-warning">
            sample data
          </span>
        </div>

        <div className="mt-5 flex flex-wrap gap-2 text-xs">
          <Pill label={`${runs.length} total`} />
          <Pill label={`${counts.ok ?? 0} ok`} dot="bg-success" />
          <Pill label={`${counts.blocked ?? 0} blocked`} dot="bg-warning" />
          <Pill label={`${counts.rejected ?? 0} rejected`} dot="bg-danger" />
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="overflow-hidden rounded-xl border border-border bg-surface">
            {runs.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                selected={run.id === selectedId}
                onSelect={() => setSelectedId(run.id)}
              />
            ))}
          </div>
          <RunDetail run={selected} />
        </div>
      </main>
    </div>
  );
}

function Pill({ label, dot }: { label: string; dot?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-muted-foreground">
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />}
      {label}
    </span>
  );
}
