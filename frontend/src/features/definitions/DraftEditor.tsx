"use client";
import { useState } from "react";
import type { DefinitionCase, DefinitionContent } from "@/types";

// Editing a workspace's draft of a workflow (ADR-0039 step 5c): its name, rules, steps, code,
// test cases and forms. Nothing here is trusted: the server bounds what is saved, runs the code
// only in the sandbox, and publishes only through the gate. This editor only keeps the page from
// sending something it can already see is not JSON.

const TABS = ["Overview", "Rules", "Steps", "Code", "Tests", "Forms"] as const;
type Tab = (typeof TABS)[number];

const KINDS = ["function", "check", "letters", "wait", "decide", "branch"] as const;
type Step = Record<string, unknown> & { id: string; kind: string };

const DEFAULT_STEPS = (functions: string[]): Step[] => [
  { id: "compute", kind: "function", function: "compute" },
  { id: "check", kind: "check", function: "check", of: "compute" },
  ...(functions.includes("letters") ? [{ id: "letters", kind: "letters", function: "letters", of: "compute" }] : []),
];

const mono = "font-mono text-[12px] leading-5";

/** A JSON value edited as text: kept as typed until it parses, then handed up. */
function JsonField({
  label,
  value,
  onChange,
  rows = 12,
}: {
  label: string;
  value: unknown;
  onChange: (v: unknown) => void;
  rows?: number;
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      <textarea
        spellCheck={false}
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setError("");
          } catch (err) {
            setError(err instanceof Error ? err.message : "not JSON");
          }
        }}
        className={`studio-input w-full py-2 ${mono} ${error ? "border-danger" : ""}`}
      />
      {error && <span className="mt-1 block text-[12px] text-danger">Not saved until it is JSON: {error}</span>}
    </label>
  );
}

function Text({ label, value, onChange, rows }: { label: string; value: string; onChange: (v: string) => void; rows?: number }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted-foreground">{label}</span>
      {rows ? (
        <textarea rows={rows} value={value} onChange={(e) => onChange(e.target.value)} className="studio-input w-full py-2" />
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} className="studio-input h-9 w-full" />
      )}
    </label>
  );
}

/** A code file: a monospace text area where Tab indents instead of leaving the field. */
function CodeArea({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <textarea
      aria-label={label}
      spellCheck={false}
      rows={Math.min(40, Math.max(8, value.split("\n").length + 1))}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key !== "Tab") return;
        e.preventDefault();
        const t = e.currentTarget;
        const { selectionStart: a, selectionEnd: b } = t;
        onChange(value.slice(0, a) + "    " + value.slice(b));
        requestAnimationFrame(() => t.setSelectionRange(a + 4, a + 4));
      }}
      className={`studio-input w-full whitespace-pre py-2 ${mono}`}
    />
  );
}

function StepsEditor({ steps, onChange }: { steps: Step[]; onChange: (s: Step[]) => void }) {
  const set = (i: number, step: Step) => onChange(steps.map((s, j) => (j === i ? step : s)));
  const move = (i: number, by: number) => {
    const next = [...steps];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    onChange(next);
  };
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Every calculation must be followed at once by its check, and letters, waits, decisions and
        branches only see checked results. Run tests to see whether the platform accepts the order.
      </p>
      {steps.map((s, i) => {
        const { id, kind, ...rest } = s;
        return (
          <div key={i} className="rounded-xl border border-border bg-white p-3">
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <Text label="Step id" value={id} onChange={(v) => set(i, { ...s, id: v })} />
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted-foreground">Kind</span>
                <select value={kind} onChange={(e) => set(i, { ...s, kind: e.target.value })} className="h-9 w-full rounded-lg border border-border bg-white px-3 text-[13px]">
                  {KINDS.map((k) => <option key={k}>{k}</option>)}
                </select>
              </label>
              <div className="flex items-end gap-1">
                <button type="button" className="studio-secondary" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button type="button" className="studio-secondary" disabled={i === steps.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button type="button" className="studio-secondary" onClick={() => onChange(steps.filter((_, j) => j !== i))}>Remove</button>
              </div>
            </div>
            <div className="mt-2">
              <JsonField
                key={`${i}-${kind}`}
                label="What the step needs (function, of, resume, decisions, on, cases, default)"
                value={rest}
                rows={Math.min(14, JSON.stringify(rest, null, 2).split("\n").length + 1)}
                onChange={(v) => v && typeof v === "object" && set(i, { ...(v as Record<string, unknown>), id, kind })}
              />
            </div>
          </div>
        );
      })}
      <button type="button" className="studio-secondary" onClick={() => onChange([...steps, { id: `step-${steps.length + 1}`, kind: "letters", of: "compute", function: "letters" }])}>
        Add a step
      </button>
    </div>
  );
}

function CodeEditor({ sources, onChange }: { sources: Record<string, string>; onChange: (s: Record<string, string>) => void }) {
  const [path, setPath] = useState("");
  const files = Object.keys(sources).sort();
  const [open, setOpen] = useState<string>(files.find((f) => f === "functions/compute.py") ?? files[0] ?? "");
  return (
    <div>
      <p className="text-sm text-muted-foreground">
        Python, run in a sandbox: no network, no files, no clock, bounded in time and memory. A
        function receives the input, the settings and the rules, and returns JSON.
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {files.map((f) => (
          <button key={f} type="button" onClick={() => setOpen(f)} className={`rounded-md border px-2 py-1 text-[12px] ${open === f ? "border-primary bg-primary/10 text-primary" : "border-border bg-white text-ink"}`}>
            {f}
          </button>
        ))}
      </div>
      {open && sources[open] !== undefined && (
        <div className="mt-3">
          <CodeArea label={open} value={sources[open]} onChange={(v) => onChange({ ...sources, [open]: v })} />
          <button
            type="button"
            className="studio-secondary mt-2"
            onClick={() => {
              const { [open]: _gone, ...rest } = sources;
              void _gone;
              onChange(rest);
              setOpen(Object.keys(rest).sort()[0] ?? "");
            }}
          >
            Remove {open}
          </button>
        </div>
      )}
      <form
        className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center"
        onSubmit={(e) => {
          e.preventDefault();
          const p = path.trim();
          if (!p.endsWith(".py") || sources[p] !== undefined) return;
          onChange({ ...sources, [p]: "" });
          setOpen(p);
          setPath("");
        }}
      >
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="e.g. mypackage/split.py" className={`studio-input h-9 w-full sm:max-w-xs ${mono}`} />
        <button className="studio-secondary" disabled={!path.trim().endsWith(".py") || sources[path.trim()] !== undefined}>Add file</button>
      </form>
    </div>
  );
}

function TestsEditor({ cases, onChange }: { cases: DefinitionCase[]; onChange: (c: DefinitionCase[]) => void }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Each case names its source, and every expected figure where it was copied from (ref). Expected
        figures come from the expert or the norm, never from a run. At least one case needs a
        counter-check.
      </p>
      {cases.map((c, i) => (
        <details key={`${i}-${c.name}`} className="rounded-xl border border-border bg-white p-3">
          <summary className="cursor-pointer text-sm font-medium text-ink">{c.name}</summary>
          <div className="mt-3">
            <JsonField
              label="The case: name, source, input, settings, expect, counter"
              value={c}
              rows={24}
              onChange={(v) => v && typeof v === "object" && onChange(cases.map((x, j) => (j === i ? (v as DefinitionCase) : x)))}
            />
            <button type="button" className="studio-secondary mt-2" onClick={() => onChange(cases.filter((_, j) => j !== i))}>
              Remove this case
            </button>
          </div>
        </details>
      ))}
      <button
        type="button"
        className="studio-secondary"
        onClick={() =>
          onChange([
            ...cases,
            {
              name: `case-${cases.length + 1}`,
              source: "",
              input: cases[0]?.input ?? {},
              settings: cases[0]?.settings ?? {},
              expect: { status: "ready", result: {} },
              counter: [],
            },
          ])
        }
      >
        Add a case
      </button>
    </div>
  );
}

export function DraftEditor({
  content,
  onChange,
}: {
  content: DefinitionContent;
  onChange: (c: DefinitionContent) => void;
}) {
  const [tab, setTab] = useState<Tab>("Overview");
  const set = (patch: Partial<DefinitionContent>) => onChange({ ...content, ...patch });
  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <div role="tablist" className="-mx-1 flex gap-1 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`shrink-0 rounded-md px-3 py-1.5 text-[13px] ${tab === t ? "bg-primary text-white" : "text-ink hover:bg-muted"}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="mt-5 space-y-4">
        {tab === "Overview" && (
          <>
            <Text label="Name" value={content.label} onChange={(v) => set({ label: v })} />
            <Text label="Summary" value={content.summary} onChange={(v) => set({ summary: v })} rows={3} />
            <label className="flex items-start gap-3 rounded-lg border border-border bg-white p-3 text-sm text-ink">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-primary"
                checked={(content.triggers ?? ["manual"]).includes("schedule")}
                onChange={(e) => set({ triggers: e.target.checked ? ["manual", "schedule"] : ["manual"] })}
              />
              <span>
                <span className="font-medium">Can run on a schedule</span>
                <span className="mt-0.5 block text-[13px] text-muted-foreground">
                  Lets agents of this workflow run on their own at set times, each run a new case from a fixed
                  input. Leave it off for work that starts from documents a person brings.
                </span>
              </span>
            </label>
            <Text label="Reading guide: how a model reads documents into the input (data, never executed)" value={content.guide} onChange={(v) => set({ guide: v })} rows={10} />
          </>
        )}
        {tab === "Rules" && (
          <>
            <Text label="Rules name" value={content.rules_name} onChange={(v) => set({ rules_name: v })} />
            <JsonField label="The rules pack, handed to every function (it needs a version)" value={content.rules} rows={28} onChange={(v) => set({ rules: v as Record<string, unknown> })} />
          </>
        )}
        {tab === "Steps" && (
          <StepsEditor steps={(content.procedure as Step[] | undefined) ?? DEFAULT_STEPS(content.functions)} onChange={(procedure) => set({ procedure })} />
        )}
        {tab === "Code" && <CodeEditor sources={content.sources} onChange={(sources) => set({ sources })} />}
        {tab === "Tests" && <TestsEditor cases={content.tests ?? []} onChange={(tests) => set({ tests })} />}
        {tab === "Forms" && (
          <>
            <JsonField label="Functions the platform calls (compute and check always; letters if it writes any)" value={content.functions} rows={3} onChange={(v) => set({ functions: v as string[] })} />
            {(["input", "settings", "result"] as const).map((f) => (
              <JsonField key={f} label={`The ${f} form (JSON Schema)`} value={(content.forms as Record<string, unknown>)[f]} rows={16} onChange={(v) => set({ forms: { ...content.forms, [f]: v } })} />
            ))}
          </>
        )}
      </div>
    </section>
  );
}
