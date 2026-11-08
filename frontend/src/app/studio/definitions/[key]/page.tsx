"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import { BuilderPanel, CheckToConfirm, FiguresToConfirm } from "@/features/definitions/BuilderPanel";
import { DraftEditor } from "@/features/definitions/DraftEditor";
import { WorkspaceDocuments } from "@/features/definitions/WorkspaceDocuments";
import type { DefinitionContent, DefinitionDetail, GateReport } from "@/types";

// One workflow definition (ADR-0039 step 5): its steps, its test cases, its guardrails, its
// history, and for the workspace's own copy the draft that is edited, tested and published here
// (step 5c). Nothing on this page changes a published version: publishing makes the draft the
// next version, and only once every case passes. The builder (step 6b) writes the draft too; while
// it runs, the draft is not changed here.

type Step = { id: string; kind: string; function?: string; of?: string; resume?: string; on?: string;
  cases?: Record<string, string>; default?: string; decisions?: { field: string; question: string }[] };

const DEFAULT_STEPS = (functions: string[]): Step[] => [
  { id: "compute", kind: "function", function: "compute" },
  { id: "check", kind: "check", function: "check", of: "compute" },
  ...(functions.includes("letters") ? [{ id: "letters", kind: "letters", function: "letters", of: "compute" }] : []),
];

/** A step in plain words: what it does, never how. */
function said(step: Step): string {
  switch (step.kind) {
    case "function":
      return `Calculates the result (${step.function}), in the sandbox.`;
    case "check":
      return `Checks the result of “${step.of}” against the input and the rules. A result it refuses is never shown.`;
    case "letters":
      return `Writes the letters from the checked result of “${step.of}”. Every amount in them must be in the result.`;
    case "wait":
      return `Waits for missing facts from a named party by a date; what stands goes out meanwhile.${step.resume ? ` Then calculates again from “${step.resume}”.` : ""}`;
    case "decide":
      return `Asks a person for a judgment, with a reason: ${(step.decisions ?? []).map((d) => d.question).join(" ")}`;
    case "branch":
      return `Goes on by the value of ${step.on}: ${Object.entries(step.cases ?? {}).map(([v, t]) => `${v} → ${t}`).join(", ")}${step.default ? `, otherwise ${step.default}` : ""}.`;
    default:
      return step.kind;
  }
}

/** Which parts of the draft differ from the published version. */
function changes(draft: DefinitionContent, published: DefinitionContent | undefined): string[] {
  if (!published) return ["Everything: nothing is published yet."];
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const out: string[] = [];
  if (draft.label !== published.label || draft.summary !== published.summary) out.push("Name or summary");
  if (!same(draft.rules, published.rules)) out.push("Rules");
  if (!same(draft.procedure, published.procedure)) out.push("Steps");
  if (!same(draft.forms, published.forms)) out.push("Forms");
  if (draft.guide !== published.guide) out.push("Reading guide");
  if (!same(draft.triggers ?? ["manual"], published.triggers ?? ["manual"])) out.push("How it may start (on a schedule or not)");
  const files = new Set([...Object.keys(draft.sources), ...Object.keys(published.sources)]);
  for (const f of [...files].sort()) if (draft.sources[f] !== published.sources[f]) out.push(`Code: ${f}`);
  const cases = new Set([...(draft.tests ?? []), ...(published.tests ?? [])].map((c) => c.name));
  for (const name of [...cases].sort()) {
    const a = draft.tests?.find((c) => c.name === name);
    const b = published.tests?.find((c) => c.name === name);
    if (!same(a, b)) out.push(`Test case: ${name}${!a ? " (removed)" : !b ? " (new)" : ""}`);
  }
  return out;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink">{title}</h2>
      {hint && <p className="mt-1 text-sm text-muted-foreground">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Cases({ content, report }: { content: DefinitionContent; report: GateReport | null }) {
  const cases = content.tests ?? [];
  if (cases.length === 0) return <p className="text-sm text-danger">No test cases: this cannot be published.</p>;
  return (
    <div className="space-y-3">
      {report?.refused && (
        <p className="rounded-lg border border-danger/20 bg-danger/5 p-3 text-sm text-danger">{report.refused}</p>
      )}
      {cases.map((c) => {
        const r = report?.cases.find((x) => x.name === c.name);
        return (
          <div key={c.name} className="rounded-xl border border-border bg-white p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-semibold text-ink">
                {c.name}
                {c.sealed && <span className="ml-2 text-[12px] font-normal text-muted-foreground">hidden from the builder</span>}
              </p>
              {r && (
                <span className={r.passed ? "text-success" : "text-danger"}>{r.passed ? "Passed" : "Failed"}</span>
              )}
            </div>
            <p className="mt-0.5 text-[13px] text-muted-foreground">Source: {c.source}</p>
            <p className="mt-2 text-ink">
              Must come out <span className="font-medium">{c.expect.status}</span>
              {c.expect.result && `, with ${Object.keys(c.expect.result).length} figures`}
              {c.expect.letters && `, letters to ${c.expect.letters.length ? c.expect.letters.join(", ") : "nobody"}`}
              {c.expect.waiting_for?.length ? `, waiting for ${c.expect.waiting_for.join(", ")}` : ""}
              {c.expect.deciding?.length ? `, asking a person about ${c.expect.deciding.join(", ")}` : ""}
              {c.counter?.length ? `; ${c.counter.length} counter-checks` : ""}.
            </p>
            {r && r.failures.length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-danger">
                {r.failures.map((f, i) => <li key={i} className="break-words">{f}</li>)}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function DefinitionPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = use(params);
  const router = useRouter();
  const [d, setD] = useState<DefinitionDetail | null>(null);
  const [view, setView] = useState<"draft" | "published">("draft");
  const [report, setReport] = useState<GateReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // A builder run is queued or running: it saves the draft when it ends.
  const [building, setBuilding] = useState(false);
  // The draft as edited on this page; saved with "Save draft".
  const [edited, setEdited] = useState<DefinitionContent | null>(null);

  const load = () =>
    api.getDefinition(key).then((detail) => {
      setD(detail);
      setView(detail.draft ? "draft" : "published");
      setEdited(detail.draft ? detail.draft.content : null);
    });

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [key]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  if (!d)
    return (
      <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
        <StudioTrail crumbs={[{ label: "Workflows", href: "/studio/definitions" }, { label: key }]} />
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : <p role="status" className="text-sm text-muted-foreground">Loading…</p>}
      </main>
    );

  const own = d.owner === "workspace";
  const editing = own && view === "draft" && d.draft !== null && edited !== null;
  const dirty = editing && JSON.stringify(edited) !== JSON.stringify(d.draft!.content);
  const content = view === "draft" && d.draft ? (edited ?? d.draft.content) : d.published?.content;
  const shownReport = view === "draft" ? report : (d.published?.gate ?? null);
  const steps: Step[] = content ? ((content.procedure as Step[] | undefined) ?? DEFAULT_STEPS(content.functions)) : [];

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Agents", href: "/studio" }, { label: "Workflows", href: "/studio/definitions" }, { label: d.label }]} />
      <p className="studio-eyebrow text-primary">{own ? "Your workflow" : "Template"}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <h1 className="text-[28px] font-semibold tracking-tight text-ink">{d.label}</h1>
      </div>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{d.summary}</p>
      {own && d.template && (
        <p className="mt-1 text-[13px] text-muted-foreground">
          Copied from the template <Link href={`/studio/definitions/${d.template}`}>{d.template}</Link>
        </p>
      )}

      {d.draft && d.published && (
        <div role="tablist" className="mt-5 inline-flex rounded-lg border border-border bg-white p-0.5 text-[13px]">
          {(["draft", "published"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={`rounded-md px-3 py-1.5 ${view === v ? "bg-primary text-white" : "text-ink"}`}
            >
              {v === "draft" ? `Draft v${d.draft!.version}` : `Published v${d.published!.version}`}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div role="alert" className="mt-6 whitespace-pre-wrap break-words rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger">
          {error}
        </div>
      )}
      {notice && <p role="status" className="mt-6 rounded-lg border border-success/25 bg-success/10 p-4 text-sm text-ink">{notice}</p>}

      {own && d.draft && view === "draft" && (
        <section className="studio-section mt-6 p-5 sm:p-6">
          <h2 className="text-lg font-semibold text-ink">Draft v{d.draft.version}</h2>
          <p className="mt-1 text-sm text-muted-foreground">What differs from {d.published ? `published v${d.published.version}` : "what is published"}:</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-ink">
            {(() => {
              const c = changes(edited ?? d.draft.content, d.published?.content);
              return c.length ? c.map((x) => <li key={x}>{x}</li>) : <li>Nothing yet.</li>;
            })()}
          </ul>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
            <button
              className="studio-primary"
              disabled={busy || building || !dirty}
              onClick={() =>
                act(async () => {
                  await api.saveDefinitionDraft(key, edited as unknown as Record<string, unknown>);
                  setReport(null);
                  await load();
                  setNotice("Draft saved. Run the tests to see how it comes out.");
                })
              }
            >
              Save draft
            </button>
            <button className="studio-secondary" disabled={busy || building || dirty} onClick={() => act(async () => setReport(await api.testDefinitionDraft(key)))}>
              {busy ? "Running…" : "Run tests"}
            </button>
            <button
              className="studio-secondary"
              disabled={busy || building || dirty}
              onClick={() =>
                act(async () => {
                  try {
                    const done = await api.publishDefinition(key);
                    setReport(null);
                    await load();
                    setNotice(`Published v${done.version}: every test case passed. Agents built on it run this version from now on.`);
                  } catch (e) {
                    setReport(await api.testDefinitionDraft(key).catch(() => null));
                    throw e;
                  }
                })
              }
            >
              Publish v{d.draft.version}
            </button>
            <button
              className="studio-secondary"
              disabled={busy || building}
              onClick={() =>
                act(async () => {
                  if (!window.confirm("Discard this draft? The published version stays as it is.")) return;
                  await api.discardDefinitionDraft(key);
                  if (d.published) await load();
                  else router.push("/studio/definitions");
                })
              }
            >
              Discard draft
            </button>
          </div>
          <p className="mt-3 text-[13px] text-muted-foreground">
            {building
              ? "The builder is working on this draft. It can be changed again once its run ends."
              : dirty
              ? "Unsaved changes: save the draft before running its tests or publishing it."
              : "Publishing runs every test case first and refuses the version if one fails. Only an administrator of this workspace publishes."}
          </p>
        </section>
      )}

      {own && !d.draft && d.published && (
        <div className="mt-6">
          <button
            className="studio-primary"
            disabled={busy || building}
            onClick={() =>
              act(async () => {
                await api.saveDefinitionDraft(key, d.published!.content as unknown as Record<string, unknown>);
                await load();
              })
            }
          >
            Edit as new draft (v{d.published.version + 1})
          </button>
          <p className="mt-2 text-[13px] text-muted-foreground">The published version keeps running until the draft is published.</p>
        </div>
      )}

      {own && (
        <BuilderPanel
          definitionKey={key}
          locked={dirty}
          onActive={setBuilding}
          onDraftWritten={() => {
            setReport(null);
            load()
              .then(() => setNotice("The builder wrote a new draft. Read what it changed, run the tests, and confirm its figures before publishing."))
              .catch((e) => setError(e instanceof Error ? e.message : String(e)));
          }}
        />
      )}

      {own && view === "draft" && d.draft && (
        <CheckToConfirm
          content={d.draft.content}
          disabled={busy || building || dirty}
          onConfirm={() =>
            act(async () => {
              await api.confirmDefinitionCheck(key);
              await load();
            })
          }
        />
      )}

      {own && view === "draft" && d.draft && (
        <FiguresToConfirm
          content={d.draft.content}
          disabled={busy || building || dirty}
          onConfirm={(testCase, path) =>
            act(async () => {
              await api.confirmDefinitionFigure(key, testCase, path);
              await load();
            })
          }
        />
      )}

      {editing && (
        <fieldset disabled={building} className="min-w-0">
          <DraftEditor key={d.draft!.version} content={edited!} onChange={setEdited} />
        </fieldset>
      )}

      {content && (
        <>
          {!editing && <Section title="Steps" hint="What a case runs through, in order. Each kind of step is the platform's; the workflow chooses the order.">
            <ol className="space-y-2">
              {steps.map((s, i) => (
                <li key={s.id} className="flex gap-3 text-sm">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-muted text-[12px] font-medium text-muted-foreground">{i + 1}</span>
                  <span>
                    <span className="font-medium text-ink">{s.id}</span>
                    <span className="ml-2 rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">{s.kind}</span>
                    <span className="mt-0.5 block text-muted-foreground">{said(s)}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Section>}

          <Section
            title="Test cases"
            hint={
              view === "draft"
                ? "Run the tests to see how the draft comes out. Every one must pass before it is published."
                : `The cases this version passed when it was published${d.published?.gate ? ` on ${d.published.gate.at.slice(0, 10)}` : ""}.`
            }
          >
            <Cases content={content} report={shownReport} />
          </Section>
        </>
      )}

      {d.guardrails.length > 0 && (
        <Section title="Guardrails" hint="What the platform enforces for the published version. None of these can be switched off.">
          <dl className="divide-y divide-border rounded-xl border border-border bg-white">
            {d.guardrails.map((g) => (
              <div key={g.question} className="grid gap-1 px-4 py-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] sm:gap-4">
                <dt className="text-sm font-medium text-ink">{g.question}</dt>
                <dd className="text-sm text-ink">
                  {g.answer}
                  <span className="mt-0.5 block text-[12px] text-muted-foreground">Enforced by {g.enforced_by}</span>
                </dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      {content && !editing && (
        <Section title="Rules and code" hint="Read-only here. The calculations run in a sandbox: no network, no files, bounded in time and memory.">
          <details className="rounded-lg border border-border bg-white p-3">
            <summary className="cursor-pointer text-[13px] font-medium text-ink">Rules: {content.rules_name} v{String(content.rules.version)}</summary>
            <pre className="mt-3 max-h-96 overflow-auto text-[11px] leading-5 text-ink">{JSON.stringify(content.rules, null, 2)}</pre>
          </details>
          {Object.keys(content.sources).sort().map((f) => (
            <details key={f} className="mt-2 rounded-lg border border-border bg-white p-3">
              <summary className="cursor-pointer text-[13px] font-medium text-ink">{f}</summary>
              <pre className="mt-3 max-h-96 overflow-auto text-[11px] leading-5 text-ink">{content.sources[f]}</pre>
            </details>
          ))}
        </Section>
      )}

      {content?.references && content.references.length > 0 && (
        <Section
          title="The law it works from"
          hint="The official texts this workflow applies, as the platform ships them. Every version keeps the text it was built with, and the builder reads all of it on every run."
        >
          <ul className="space-y-2">
            {content.references.map((r) => (
              <li key={r.name}>
                <details className="rounded-lg border border-border bg-white p-3">
                  <summary className="cursor-pointer">
                    <span className="text-[13px] font-medium text-ink">{r.title}</span>
                    <span className="mt-0.5 block break-words text-[12px] text-muted-foreground">
                      {r.stand} · fetched {r.retrieved} ·{" "}
                      <a href={r.url} target="_blank" rel="noreferrer" className="underline">official source</a>
                    </span>
                  </summary>
                  <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-5 text-ink">{r.text}</pre>
                </details>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {own && content && (
        <WorkspaceDocuments
          definitionKey={key}
          content={content}
          canChoose={view === "draft" && d.draft !== null && !building && !dirty && !busy}
          onChanged={() => void load()}
        />
      )}

      <Section title="History" hint="Published versions never change; a case keeps the version that computed it.">
        <ul className="divide-y divide-border rounded-xl border border-border bg-white text-sm">
          {d.versions.map((v) => (
            <li key={v.version} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
              <span className="text-ink">v{v.version} · {v.status}</span>
              <span className="text-muted-foreground">
                {v.published_at ? new Date(v.published_at).toLocaleString() : "not published"}
                {v.status === "published" && (v.passed === null ? " · before the test gate" : v.passed ? " · tests passed" : "")}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </main>
  );
}
