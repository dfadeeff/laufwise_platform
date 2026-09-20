"use client";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CallsView } from "@/features/history/CallsView";
import { RunsView } from "@/features/history/RunsView";

// One place to read what actually happened: conversations the voice agents held and runs the
// workflows executed. Two tiers, two shapes of evidence, one screen — because "what did it do
// yesterday?" is one question, not two.

const TABS = [
  ["calls", "Calls"],
  ["runs", "Workflow runs"],
] as const;
type Tab = (typeof TABS)[number][0];

function History() {
  const params = useSearchParams();
  const router = useRouter();
  // The URL owns the tab, so a link can open either half — and a deep link to one call (from a
  // test or a follow-up) opens the calls tab whatever the tab parameter says.
  const tab: Tab =
    params.get("tab") === "runs" && !params.get("call") ? "runs" : "calls";
  return (
    <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-10">
      <h1 className="text-3xl font-semibold tracking-tight text-ink">
        Run history
      </h1>
      <div
        role="tablist"
        aria-label="Run history"
        className="mt-5 flex gap-1 border-b border-border"
      >
        {TABS.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => router.replace(`/studio/history?tab=${key}`)}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm transition ${tab === key ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-ink"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="mt-6">
        {tab === "calls" ? <CallsView /> : <RunsView />}
      </div>
    </main>
  );
}

export default function HistoryPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8">
          <p className="text-sm text-muted-foreground">Loading history…</p>
        </main>
      }
    >
      <History />
    </Suspense>
  );
}
