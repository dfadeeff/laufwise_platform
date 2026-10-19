"use client";
import { use, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import { SchemaForm } from "@/features/workflows/SchemaForm";
import { ResultView } from "@/features/workflows/ResultView";
import { CaseStatusChip } from "@/features/workflows/CaseStatusChip";
import type { CaseDetail, StoredFile } from "@/types";

// One case (ADR-0030): documents in, the input a person checks and corrects, the result the
// platform computes from it. The result has no editor: a wrong figure is fixed in the input and
// computed again, and every save and computation stays in the history below.

const EVENTS: Record<string, string> = {
  created: "Case created",
  input_saved: "Input saved",
  computed: "Computed",
  file_attached: "Document attached",
  file_detached: "Document detached",
};

const kib = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`);

function Notice({ tone, title, items }: { tone: "danger" | "warning" | "muted"; title: string; items: string[] }) {
  const tones = {
    danger: "border-danger/20 bg-danger/5 text-danger",
    warning: "border-warning/25 bg-warning/10 text-ink",
    muted: "border-border bg-surface text-ink",
  };
  return (
    <div className={`rounded-xl border p-4 text-sm ${tones[tone]}`}>
      <p className="font-semibold">{title}</p>
      <ul className="mt-2 list-disc space-y-1.5 pl-5 leading-6">
        {items.map((q, i) => (
          <li key={i}>{q}</li>
        ))}
      </ul>
    </div>
  );
}

export default function CasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = use(params);
  const [c, setCase] = useState<CaseDetail | null>(null);
  const [input, setInput] = useState<Record<string, unknown>>({});
  const [mode, setMode] = useState<"form" | "json">("form");
  const [json, setJson] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [files, setFiles] = useState<StoredFile[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const adopt = (detail: CaseDetail) => {
    setCase(detail);
    setInput(detail.input);
    setJson(JSON.stringify(detail.input, null, 2));
    setJsonError("");
  };

  useEffect(() => {
    api.getCase(caseId).then(adopt).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    api.listFiles().then(setFiles).catch(() => setFiles([]));
  }, [caseId]);

  const act = async (fn: () => Promise<CaseDetail | void>) => {
    setBusy(true);
    setError("");
    try {
      const detail = await fn();
      if (detail) adopt(detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  if (!c)
    return (
      <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
        <StudioTrail crumbs={[{ label: "Agents", href: "/studio" }, { label: "Case" }]} />
        {error ? (
          <p role="alert" className="text-sm text-danger">{error}</p>
        ) : (
          <p role="status" className="text-sm text-muted-foreground">Loading…</p>
        )}
      </main>
    );

  const dirty = JSON.stringify(input) !== JSON.stringify(c.input);
  const attachedIds = new Set(c.files.map((f) => f.id));
  const attachable = files.filter((f) => !attachedIds.has(f.id));

  const upload = (chosen: File[]) =>
    act(async () => {
      let detail: CaseDetail | undefined;
      for (const file of chosen) {
        const stored = await api.uploadFile(file);
        detail = await api.attachCaseFile(c.id, stored.id);
      }
      api.listFiles().then(setFiles).catch(() => {});
      return detail;
    });

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail
        crumbs={[
          { label: "Agents", href: "/studio" },
          ...(c.agent ? [{ label: c.agent.name, href: `/studio/workflow-agents/${c.agent.id}` }] : []),
          { label: c.title },
        ]}
      />
      <p className="studio-eyebrow text-primary">{c.agent?.workflow.label ?? "Case"}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <h1 className="text-[28px] font-semibold tracking-tight text-ink">{c.title}</h1>
        <CaseStatusChip status={c.status} />
      </div>
      <p className="mt-2 text-[13px] text-muted-foreground">
        Input revision {c.input_revision}
        {c.computed_revision !== null &&
          ` · computed from revision ${c.computed_revision}${c.rules_version ? ` with ${c.rules_version}` : ""}`}
      </p>

      {error && (
        <div role="alert" className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Result */}
      <section className="mt-8 space-y-4">
        <h2 className="text-lg font-semibold text-ink">Result</h2>
        {c.errors.length > 0 && <Notice tone="danger" title="The input cannot be computed yet" items={c.errors} />}
        {c.questions.length > 0 && (
          <Notice
            tone="warning"
            title="Open questions. No figures until they are answered, in the input or in the agent's settings."
            items={c.questions}
          />
        )}
        {c.problems.length > 0 && (
          <Notice tone="danger" title="The check refused this result, so it is not shown" items={c.problems} />
        )}
        {c.computed_revision === null && (
          <p className="text-sm text-muted-foreground">Not computed since the input was last saved.</p>
        )}
        {c.status === "ready" && c.result && c.schemas && (
          <div className="studio-section p-5 sm:p-6">
            <ResultView schema={c.schemas.result} value={c.result} />
          </div>
        )}
        {c.assumptions.length > 0 && <Notice tone="muted" title="Assumptions stated with this result" items={c.assumptions} />}
      </section>

      {/* Documents */}
      <section className="studio-section mt-8 p-5 sm:p-6">
        <h2 className="text-lg font-semibold text-ink">Documents</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          What this case is computed from. A document attached here cannot be deleted from Files.
        </p>
        {c.files.length > 0 && (
          <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-white">
            {c.files.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <span className="min-w-0 truncate font-medium text-ink" title={f.name}>
                  {f.name}
                  <span className="ml-2 font-normal text-muted-foreground">{kib(f.size_bytes)}</span>
                </span>
                <span className="flex gap-2">
                  <button
                    className="studio-secondary"
                    onClick={async () => {
                      try {
                        const url = URL.createObjectURL(await api.downloadFile(f.id));
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = f.name;
                        a.click();
                        URL.revokeObjectURL(url);
                      } catch (e) {
                        setError(e instanceof Error ? e.message : String(e));
                      }
                    }}
                  >
                    Download
                  </button>
                  <button
                    className="studio-secondary"
                    disabled={busy}
                    onClick={() => act(() => api.detachCaseFile(c.id, f.id))}
                  >
                    Detach
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center">
          <button className="studio-secondary" disabled={busy} onClick={() => picker.current?.click()}>
            <Icon name="plus" size={14} width={2.2} />
            Upload documents
          </button>
          <input
            ref={picker}
            type="file"
            multiple
            className="sr-only"
            accept=".pdf,.png,.jpg,.jpeg,.txt,.csv"
            onChange={(e) => {
              upload(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          {attachable.length > 0 && (
            <select
              aria-label="Attach a file already in this workspace"
              value=""
              disabled={busy}
              onChange={(e) => e.target.value && act(() => api.attachCaseFile(c.id, e.target.value))}
              className="h-9 rounded-lg border border-border bg-white px-3 text-[13px] text-ink sm:max-w-xs"
            >
              <option value="">Attach a file from Files…</option>
              {attachable.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </section>

      {/* Input */}
      <details
        key={`input-${c.status}`}
        open={c.status !== "ready"}
        className="studio-section group mt-6 p-5 sm:p-6"
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
          <span>
            <span className="block text-lg font-semibold text-ink">Input</span>
            <span className="mt-1 block text-sm text-muted-foreground">
              What was read from the documents. Correct it here; the result is always computed
              from it.
            </span>
          </span>
          <Icon name="chevronDown" className="text-muted-foreground transition group-open:rotate-180" />
        </summary>
        <div className="mt-5 flex justify-end">
          <div className="flex gap-1 rounded-lg bg-muted p-0.5" role="tablist" aria-label="Input view">
            {(["form", "json"] as const).map((m) => (
              <button
                key={m}
                role="tab"
                aria-selected={mode === m}
                onClick={() => {
                  if (m === "json") setJson(JSON.stringify(input, null, 2));
                  setMode(m);
                }}
                className={`rounded-[7px] px-2.5 py-1 text-[13px] ${mode === m ? "bg-white font-medium text-ink shadow-sm" : "text-muted-foreground"}`}
              >
                {m === "form" ? "Form" : "JSON"}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-5">
          {mode === "form" && c.schemas ? (
            <SchemaForm schema={c.schemas.input} value={input} onChange={setInput} />
          ) : (
            <>
              <textarea
                aria-label="Input as JSON"
                value={json}
                spellCheck={false}
                onChange={(e) => {
                  setJson(e.target.value);
                  try {
                    setInput(JSON.parse(e.target.value));
                    setJsonError("");
                  } catch (err) {
                    setJsonError(err instanceof Error ? err.message : "not valid JSON");
                  }
                }}
                className="h-[420px] w-full rounded-lg border border-border bg-white p-3 font-mono text-[12px] text-ink outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
              />
              {jsonError && <p className="mt-2 text-sm text-danger">{jsonError}</p>}
            </>
          )}
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            className="studio-secondary"
            disabled={!dirty || busy || Boolean(jsonError)}
            onClick={() => act(() => api.saveCaseInput(c.id, c.input_revision, input))}
          >
            Save input
          </button>
          <button
            className="studio-primary"
            disabled={dirty || busy}
            title={dirty ? "Save the input first" : undefined}
            onClick={() => act(() => api.computeCase(c.id))}
          >
            Compute
          </button>
          {dirty && <span className="text-sm text-muted-foreground">Unsaved changes. Save, then compute.</span>}
        </div>
      </details>

      {/* History */}
      <section className="mt-10">
        <h2 className="text-[15px] font-semibold text-ink">History</h2>
        <ol className="mt-3 space-y-1.5 text-[13px] text-muted-foreground">
          {[...c.events].reverse().map((e, i) => (
            <li key={i}>
              <span className="text-ink">{EVENTS[e.kind] ?? e.kind}</span>
              {e.name && ` · ${e.name}`}
              {e.revision !== undefined && ` · revision ${e.revision}`}
              {e.status && ` · ${e.status}`}
              {e.created_at && <span className="ml-2 text-faint">{new Date(e.created_at).toLocaleString()}</span>}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
