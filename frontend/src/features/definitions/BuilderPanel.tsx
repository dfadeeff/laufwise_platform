"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { BuilderRun, DefinitionContent, StoredFile } from "@/types";

// The builder in the Studio (ADR-0039 step 6b). A person says what to change and attaches the
// documents; the builder writes the draft, runs its test cases and revises it, at most four times.
// It never publishes, and every figure it writes waits for a person to confirm it against its
// source. While it runs, the draft is not changed by hand: the server refuses that too.

const ACTIVE = new Set(["queued", "running"]);
const POLL_MS = 3000;

const STATE: Record<BuilderRun["state"], { label: string; tone: string }> = {
  queued: { label: "Waiting to start", tone: "text-muted-foreground" },
  running: { label: "Working…", tone: "text-primary" },
  succeeded: { label: "Draft written", tone: "text-success" },
  failed: { label: "Failed", tone: "text-danger" },
  interrupted: { label: "Interrupted", tone: "text-danger" },
};

function Run({ run, files }: { run: BuilderRun; files: StoredFile[] }) {
  const s = STATE[run.state] ?? { label: run.state, tone: "text-ink" };
  const named = run.file_ids.map((id) => files.find((f) => f.id === id)?.name ?? "a removed file");
  const last = run.rounds[run.rounds.length - 1];
  return (
    <details open={ACTIVE.has(run.state) || undefined} className="rounded-xl border border-border bg-white p-4 text-sm">
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className={`font-medium ${s.tone}`}>{s.label}</span>
          <span className="text-[12px] text-muted-foreground">
            {run.created_at ? new Date(run.created_at).toLocaleString() : ""}
            {run.draft_version ? ` · draft v${run.draft_version}` : ""}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 break-words text-ink">{run.instruction}</p>
      </summary>
      <div className="mt-3 space-y-3">
        {named.length > 0 && <p className="text-[13px] text-muted-foreground">Documents: {named.join(", ")}</p>}
        {run.error && <p className="rounded-lg border border-danger/20 bg-danger/5 p-3 text-danger">{run.error}</p>}
        {run.summary && (
          <div>
            <p className="text-xs font-medium text-muted-foreground">What the builder says it did</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-ink">{run.summary}</p>
          </div>
        )}
        {run.rounds.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground">Each try, as the platform tested it</p>
            <ol className="mt-1 space-y-2">
              {run.rounds.map((r) => (
                <li key={r.round} className="rounded-lg border border-border p-3">
                  <p className="font-medium text-ink">
                    Try {r.round}:{" "}
                    {!r.applied ? (
                      <span className="text-danger">not applied</span>
                    ) : r.passed ? (
                      <span className="text-success">every test case passed</span>
                    ) : (
                      <span className="text-danger">{r.failures?.length ?? 0} failures</span>
                    )}
                  </p>
                  {r.error && <p className="mt-1 break-words text-danger">{r.error}</p>}
                  {r.changed && r.changed.length > 0 && <p className="mt-1 break-words text-muted-foreground">Changed: {r.changed.join(", ")}</p>}
                  {r.failures && r.failures.length > 0 && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-5 text-danger">
                      {r.failures.map((f, i) => <li key={i} className="break-words">{f}</li>)}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </div>
        )}
        {run.state === "succeeded" && last && !last.passed && (
          <p className="text-[13px] text-danger">
            The builder stopped after {run.rounds.length} tries with test cases still failing. The draft is saved as it left it; it cannot be published until they pass.
          </p>
        )}
        {run.guardrail_changes.length > 0 && (
          <div>
            <p className="text-xs font-medium text-muted-foreground">Guardrails that change</p>
            <dl className="mt-1 divide-y divide-border rounded-lg border border-border">
              {run.guardrail_changes.map((g) => (
                <div key={g.question} className="px-3 py-2">
                  <dt className="font-medium text-ink">{g.question}</dt>
                  <dd className="mt-0.5 break-words text-muted-foreground">
                    {g.before ? <><s>{g.before}</s> → </> : null}
                    <span className="text-ink">{g.after}</span>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        {run.model && <p className="text-[12px] text-muted-foreground">Model: {run.model}</p>}
      </div>
    </details>
  );
}

export function BuilderPanel({
  definitionKey,
  locked,
  onActive,
  onDraftWritten,
}: {
  definitionKey: string;
  /** The page has unsaved edits: a run would write over the draft they belong to. */
  locked: boolean;
  onActive: (active: boolean) => void;
  onDraftWritten: () => void;
}) {
  const [runs, setRuns] = useState<BuilderRun[] | null>(null);
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const seen = useRef<Map<string, string>>(new Map());

  const active = (runs ?? []).some((r) => ACTIVE.has(r.state));

  const refresh = async () => {
    const next = await api.builderRuns(definitionKey);
    // A run that has just written the draft: the page shows the new draft.
    const wrote = next.some((r) => r.state === "succeeded" && seen.current.has(r.id) && seen.current.get(r.id) !== "succeeded");
    seen.current = new Map(next.map((r) => [r.id, r.state]));
    setRuns(next);
    if (wrote) onDraftWritten();
  };

  useEffect(() => {
    refresh().catch((e) => setError(e instanceof Error ? e.message : String(e)));
    api.listFiles().then(setFiles).catch(() => setFiles([]));
  }, [definitionKey]);

  useEffect(() => {
    onActive(active);
    if (!active) return;
    const t = setInterval(() => refresh().catch(() => {}), POLL_MS);
    return () => clearInterval(t);
  }, [active]);

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setError("");
    try {
      const added: StoredFile[] = [];
      for (const f of Array.from(list)) added.push(await api.uploadFile(f));
      setFiles((fs) => [...added, ...fs.filter((f) => !added.some((a) => a.id === f.id))]);
      setChosen((c) => [...new Set([...c, ...added.map((a) => a.id)])]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      await api.askBuilder(definitionKey, instruction.trim(), chosen);
      setInstruction("");
      setChosen([]);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink">Builder</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        Say what to build or change, and attach what it should work from: the task, the contract, an
        expert&apos;s solution. The builder writes the draft, runs every test case and revises it up to
        four times. It never publishes, and every figure it writes stays unconfirmed until you check
        it against its source.
      </p>

      <label className="mt-4 block">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">Instruction</span>
        <textarea
          rows={5}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="e.g. Add the case from the attached statement and solution, and make the heating costs split by consumption."
          className="studio-input w-full py-2"
          disabled={active}
        />
      </label>

      <div className="mt-3">
        <p className="mb-1 text-xs font-medium text-muted-foreground">Documents</p>
        {files.length > 0 && (
          <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border bg-white p-2">
            {files.map((f) => (
              <label key={f.id} className="flex items-center gap-2 text-[13px] text-ink">
                <input
                  type="checkbox"
                  checked={chosen.includes(f.id)}
                  disabled={active}
                  onChange={(e) => setChosen((c) => (e.target.checked ? [...c, f.id] : c.filter((x) => x !== f.id)))}
                />
                <span className="min-w-0 truncate">{f.name}</span>
              </label>
            ))}
          </div>
        )}
        <label className="studio-secondary mt-2 inline-flex cursor-pointer">
          Upload documents
          <input type="file" multiple className="sr-only" disabled={busy || active} onChange={(e) => { void upload(e.target.files); e.target.value = ""; }} />
        </label>
      </div>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <button className="studio-primary" disabled={busy || active || locked || !instruction.trim()} onClick={start}>
          {active ? "The builder is working…" : "Start the builder"}
        </button>
        <span className="text-[13px] text-muted-foreground">
          {locked
            ? "Save or undo your edits first: the builder writes over the draft."
            : active
              ? "The draft cannot be changed by hand until the run ends. This page updates by itself."
              : "A run takes a few minutes."}
        </span>
      </div>
      {error && <p role="alert" className="mt-3 break-words text-sm text-danger">{error}</p>}

      {runs && runs.length > 0 && (
        <div className="mt-6 space-y-3">
          <h3 className="text-sm font-semibold text-ink">Runs</h3>
          {runs.map((r) => <Run key={r.id} run={r} files={files} />)}
        </div>
      )}
    </section>
  );
}

/** Every expected figure in the draft that waits for a person to check it against its source. */
export function unverifiedFigures(content: DefinitionContent | undefined) {
  return (content?.tests ?? []).flatMap((c) =>
    Object.entries(c.expect.result ?? {})
      .filter(([, f]) => f.unverified)
      .map(([path, f]) => ({ case: c.name, source: c.source, path, value: f.value, ref: f.ref })),
  );
}

export function FiguresToConfirm({
  content,
  disabled,
  onConfirm,
}: {
  content: DefinitionContent;
  disabled: boolean;
  onConfirm: (testCase: string, path: string) => void;
}) {
  const figures = unverifiedFigures(content);
  if (figures.length === 0) return null;
  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink">Figures to confirm ({figures.length})</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        The builder wrote these expected figures. Open the source each case names, find the figure
        where the reference says, and confirm it only if it is there. Until every one is confirmed,
        this draft cannot be published. Your name is recorded with each.
      </p>
      <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-white">
        {figures.map((f) => (
          <li key={`${f.case}:${f.path}`} className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-4">
            <div className="min-w-0">
              <p className="break-words text-ink">
                <span className="font-medium">{f.case}</span> · <code className="text-[12px]">{f.path}</code> ={" "}
                <span className="font-semibold">{String(f.value)}</span>
              </p>
              <p className="mt-0.5 break-words text-[13px] text-muted-foreground">Where: {f.ref || "no reference given"} · Source: {f.source || "none named"}</p>
            </div>
            <button className="studio-secondary" disabled={disabled} onClick={() => onConfirm(f.case, f.path)}>
              Confirmed against source
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
