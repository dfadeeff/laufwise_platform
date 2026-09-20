"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** The workflow half of Run history: what each governed run did, step by verified step. */
export function RunsView() {
  const [runs, setRuns] = useState<Awaited<
    ReturnType<typeof api.listRuns>
  > | null>(null);
  const [selected, setSelected] = useState<Awaited<
    ReturnType<typeof api.getRun>
  > | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .listRuns()
      .then(setRuns)
      .catch((e) => setError(e.message));
  }, []);
  async function select(id: string) {
    setError("");
    setSelected(null);
    try {
      setSelected(await api.getRun(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <div>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Persisted results for every workflow run in this practice — the
        engine&rsquo;s own record, not a summary of it.
      </p>
      {error && (
        <p role="alert" className="mt-5 text-sm text-danger">
          {error}
        </p>
      )}
      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <section className="studio-section">
          {runs === null ? (
            <p className="text-sm">Loading runs…</p>
          ) : runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No saved workflow runs for this practice yet.
            </p>
          ) : (
            runs.map((r) => (
              <button
                key={r.run_id}
                onClick={() => select(r.run_id)}
                className="flex w-full items-center justify-between gap-3 border-b border-border py-4 text-left last:border-0"
              >
                <div>
                  <p className="text-sm font-medium">
                    {r.runbook.replaceAll("_", " ")}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {new Date(r.started_at).toLocaleString()} · v{r.version}
                  </p>
                </div>
                <span className="chip">{r.status}</span>
              </button>
            ))
          )}
        </section>
        <section className="studio-section">
          <h2 className="font-semibold">Execution details</h2>
          {!selected ? (
            <p className="mt-4 text-sm text-muted-foreground">
              Select a run to inspect its verified results.
            </p>
          ) : (
            selected.steps.map((step) => (
              <div
                key={step.step_id}
                className="mt-4 border-t border-border pt-4"
              >
                <p className="text-sm font-medium">
                  {step.step_id.replaceAll("_", " ")}{" "}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {step.status}
                  </span>
                </p>
                {step.reason && (
                  <p className="mt-2 text-sm text-muted-foreground">
                    {step.reason}
                  </p>
                )}
                {step.expr && (
                  <code className="mt-2 block break-all text-xs">
                    {step.expr}
                  </code>
                )}
              </div>
            ))
          )}
        </section>
      </div>
    </div>
  );
}
