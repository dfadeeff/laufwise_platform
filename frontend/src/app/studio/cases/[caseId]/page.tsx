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
  extracted: "Read from the documents",
  approval_requested: "Approval requested",
  approval_decided: "Approval decided",
  approval_withdrawn: "Approval withdrawn: the case changed",
  approval_expired: "Approval expired",
  letters_sent: "Letters sent",
};

const kib = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** One letter: written from the result by the workflow, checked against it, never edited here.
 *  A different wording is a change to the workflow's template, not to this text. */
function Letter({
  caseId,
  index,
  draft,
}: {
  caseId: string;
  index: number;
  draft: CaseDetail["drafts"][number];
}) {
  const [copied, setCopied] = useState(false);
  const [pdfError, setPdfError] = useState("");
  return (
    <div className="studio-section p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[15px] font-semibold text-ink">{draft.recipient}</p>
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground" title={draft.title}>
            {draft.title}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="studio-secondary"
            onClick={async () => {
              await navigator.clipboard.writeText(`${draft.title}\n\n${draft.text}`);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button
            className="studio-secondary"
            onClick={async () => {
              // The letter as the tenant receives it (ADR-0037): letterhead, today's date, and
              // the enclosure on its own pages. Rendered by the server from the checked text.
              setPdfError("");
              try {
                const url = URL.createObjectURL(await api.downloadLetterPdf(caseId, index));
                const a = document.createElement("a");
                a.href = url;
                a.download = `${draft.title} – ${draft.recipient}.pdf`;
                a.click();
                URL.revokeObjectURL(url);
              } catch (e) {
                setPdfError(e instanceof Error ? e.message : String(e));
              }
            }}
          >
            PDF
          </button>
        </div>
      </div>
      {pdfError && <p className="mt-2 text-sm text-danger">{pdfError}</p>}
      <p className="mt-4 whitespace-pre-wrap text-sm leading-6 text-ink">{draft.text}</p>
      {draft.enclosure && (
        <details className="mt-4 rounded-lg border border-border bg-surface/60 p-3">
          <summary className="cursor-pointer text-[13px] font-medium text-ink">
            Enclosed: {draft.enclosure_name}
          </summary>
          <pre className="mt-3 overflow-x-auto text-[11px] leading-5 text-ink">{draft.enclosure}</pre>
        </details>
      )}
    </div>
  );
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** Sending the letters: request, the decision by a second administrator, what was delivered.
 *  The page only shows what the server allows; the server checks every rule again. */
function Sending({
  c,
  busy,
  act,
}: {
  c: CaseDetail;
  busy: boolean;
  act: (fn: () => Promise<CaseDetail | void>) => void;
}) {
  const a = c.approval;
  const [emails, setEmails] = useState<Record<string, string>>(
    () => Object.fromEntries((a?.letters ?? []).map((l) => [l.recipient, l.email])),
  );
  const [note, setNote] = useState("");
  const earlier = a?.status === "approved" && !a.current;
  const open = !a || ["rejected", "withdrawn", "expired"].includes(a.status) || earlier;
  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink">Send the letters</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        A second administrator approves before anything is sent. Changing the case afterwards
        withdraws the request.
      </p>

      {earlier && (
        <p className="mt-4 rounded-lg border border-border bg-surface p-3 text-sm text-ink">
          Earlier letters for this case were sent on {when(a.decided_at)}. The case has changed
          since; these corrected letters need their own approval.
        </p>
      )}

      {a && a.status !== "pending" && a.status !== "approved" && (
        <p className="mt-4 rounded-lg border border-border bg-surface p-3 text-sm text-ink">
          The last request was <strong>{a.status}</strong>
          {a.note && <>: {a.note}</>}
        </p>
      )}

      {open && (
        <form
          className="mt-5 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            act(() => api.requestApproval(c.id, emails));
          }}
        >
          {c.drafts.map((d) => (
            <label key={d.recipient} className="grid gap-1 sm:grid-cols-[200px_minmax(0,1fr)] sm:items-center">
              <span className="text-sm text-ink">{d.recipient}</span>
              <input
                type="email"
                required
                value={emails[d.recipient] ?? ""}
                onChange={(e) => setEmails({ ...emails, [d.recipient]: e.target.value })}
                placeholder="E-Mail-Adresse"
                className="studio-input"
              />
            </label>
          ))}
          <button className="studio-primary" disabled={busy}>
            Request approval
          </button>
        </form>
      )}

      {a?.status === "pending" && (
        <div className="mt-5 rounded-xl border border-warning/25 bg-warning/10 p-4 text-sm text-ink">
          <p className="font-semibold">
            Waiting for approval, requested by {a.requested_by_me ? "you" : "another member"} on{" "}
            {when(a.requested_at)}. Expires {when(a.expires_at)}.
          </p>
          <ul className="mt-2 list-disc pl-5">
            {a.letters.map((l) => (
              <li key={l.recipient}>
                {l.recipient} → {l.email}
              </li>
            ))}
          </ul>
          {a.can_decide ? (
            <div className="mt-4 space-y-3">
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Note (required to reject)"
                className="studio-input h-20 w-full py-2"
              />
              <div className="flex flex-wrap gap-2">
                <button
                  className="studio-primary"
                  disabled={busy}
                  onClick={() => act(() => api.decideApproval(a.id, true, note))}
                >
                  Approve and send
                </button>
                <button
                  className="studio-secondary"
                  disabled={busy || !note.trim()}
                  onClick={() => act(() => api.decideApproval(a.id, false, note))}
                >
                  Reject
                </button>
              </div>
            </div>
          ) : (
            <p className="mt-3 text-muted-foreground">
              {a.requested_by_me
                ? "Another administrator of this workspace must approve it."
                : "Only an administrator of this workspace can approve it."}
            </p>
          )}
        </div>
      )}

      {a?.status === "approved" && a.current && (
        <div className="mt-5 space-y-3">
          <p className="text-sm text-ink">
            Approved by {a.decided_by_me ? "you" : "another administrator"} on {when(a.decided_at)}.
          </p>
          <ul className="divide-y divide-border rounded-xl border border-border bg-white text-sm">
            {a.deliveries.map((d) => (
              <li key={d.recipient} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                <span className="text-ink">
                  {d.recipient} <span className="text-muted-foreground">· {d.email}</span>
                </span>
                <span className={d.status === "sent" ? "text-success" : "text-danger"}>
                  {d.status === "sent" ? `Sent ${when(d.at)}` : `Not sent: ${d.error}`}
                </span>
              </li>
            ))}
          </ul>
          {a.deliveries.some((d) => d.status === "failed") && (
            <button
              className="studio-secondary"
              disabled={busy}
              onClick={() => act(() => api.sendApprovedAgain(a.id))}
            >
              Send the failed letters again
            </button>
          )}
        </div>
      )}
    </section>
  );
}

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
        {c.status === "ready" && c.drafts.length > 0 && (
          <div className="space-y-4 pt-4">
            <h2 className="text-lg font-semibold text-ink">Letters</h2>
            <p className="text-sm text-muted-foreground">
              Written from the result; every amount in them was checked against it.
            </p>
            {c.drafts.map((d, i) => (
              <Letter key={d.recipient} caseId={c.id} index={i} draft={d} />
            ))}
          </div>
        )}
        {c.status === "ready" && c.drafts.length > 0 && (
          <Sending key={c.approval?.id ?? "none"} c={c} busy={busy} act={act} />
        )}
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
          {c.files.length > 0 && (
            <button
              className="studio-primary"
              disabled={busy}
              onClick={() => {
                if (
                  c.input_revision > 1 &&
                  !window.confirm("Reading the documents replaces the current input. Continue?")
                )
                  return;
                act(() => api.extractCase(c.id));
              }}
            >
              {busy ? "Working…" : "Read documents into the input"}
            </button>
          )}
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
        {c.input_source === "extracted" && (
          <div className="mt-5 rounded-xl border border-warning/25 bg-warning/10 p-4 text-sm text-ink">
            <p className="font-semibold">
              Read from the documents by a model. Check every value against the documents,
              correct what is wrong, then confirm. It is not computed before that.
            </p>
            {c.to_check.length > 0 ? (
              <>
                <p className="mt-2">Not found in the documents&apos; text — check these first:</p>
                <ul className="mt-1.5 list-disc space-y-1 pl-5">
                  {c.to_check.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="mt-2 text-muted-foreground">
                Every amount and date read was found in the documents&apos; text. That does not
                prove it is in the right field.
              </p>
            )}
          </div>
        )}
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
            disabled={(!dirty && c.input_source !== "extracted") || busy || Boolean(jsonError)}
            onClick={() => act(() => api.saveCaseInput(c.id, c.input_revision, input))}
          >
            {c.input_source === "extracted" && !dirty ? "Confirm input" : "Save input"}
          </button>
          <button
            className="studio-primary"
            disabled={dirty || busy || c.input_source === "extracted"}
            title={
              c.input_source === "extracted"
                ? "Confirm the input first"
                : dirty
                  ? "Save the input first"
                  : undefined
            }
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
              {e.model && ` · ${e.model}`}
              {e.created_at && <span className="ml-2 text-faint">{new Date(e.created_at).toLocaleString()}</span>}
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}
