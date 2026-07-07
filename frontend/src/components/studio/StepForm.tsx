"use client";

// One card per step in the authoring tier's step-form editor (ADR-0002: an ordered
// list of step forms — not a graph, not raw YAML). Trace steps are surface markers
// and render muted; enforced steps expose the full governed loop: preconditions ->
// tool allowlist -> approval -> execute -> postconditions.

import { useState } from "react";
import { Field, TagInput, inputCls } from "@/components/studio/ui";
import { newRowKey } from "@/components/studio/ParameterSchemaForm";
import type { StepDef, StepKind } from "@/types";

export interface CheckRow {
  key: string; // client-only list key
  expr: string;
  reason: string;
}

export interface EditableStep {
  key: string; // client-only list key
  id: string;
  kind: StepKind;
  description: string;
  tools: string[];
  preconditions: CheckRow[];
  postconditions: CheckRow[];
  approvalRequiredWhen: string;
  approvalPrompt: string;
  executeAdapter: string;
  executeTool: string;
}

export function blankStep(): EditableStep {
  return {
    key: newRowKey(),
    id: "",
    kind: "enforced",
    description: "",
    tools: [],
    preconditions: [],
    postconditions: [],
    approvalRequiredWhen: "",
    approvalPrompt: "",
    executeAdapter: "stub",
    executeTool: "",
  };
}

export function stepToEditable(step: StepDef): EditableStep {
  const checks = (defs?: StepDef["preconditions"]): CheckRow[] =>
    (defs ?? []).map((c) => ({ key: newRowKey(), expr: c.expr ?? "", reason: c.reason ?? "" }));
  return {
    key: newRowKey(),
    id: step.id,
    kind: step.kind,
    description: step.description ?? "",
    tools: step.tools ?? [],
    preconditions: checks(step.preconditions),
    postconditions: checks(step.postconditions),
    approvalRequiredWhen: step.approval?.required_when ?? "",
    approvalPrompt: step.approval?.prompt ?? "",
    executeAdapter: step.execute?.adapter ?? "stub",
    executeTool: step.execute?.tool ?? "",
  };
}

export function editableToStep(step: EditableStep): StepDef {
  if (step.kind === "trace") {
    return { id: step.id.trim(), kind: "trace", description: step.description };
  }
  const checks = (rows: CheckRow[]) =>
    rows
      .filter((c) => c.expr.trim())
      .map((c) => ({ expr: c.expr.trim(), reason: c.reason.trim() || null }));
  const hasApproval = step.approvalRequiredWhen.trim() || step.approvalPrompt.trim();
  const tool = step.executeTool.trim();
  return {
    id: step.id.trim(),
    kind: "enforced",
    description: step.description,
    tools: step.tools,
    preconditions: checks(step.preconditions),
    postconditions: checks(step.postconditions),
    approval: hasApproval
      ? {
          required_when: step.approvalRequiredWhen.trim() || null,
          prompt: step.approvalPrompt.trim() || null,
        }
      : null,
    execute: tool ? { adapter: step.executeAdapter, tool } : null,
    on_fail: "halt",
  };
}

/** Client-side mirror of the publish gate (backend/app/templates/validation.py) —
 *  drafts are workbenches, so these only warn; the gate itself refuses at publish. */
export function gateWarnings(step: EditableStep, declaredBindings: string[]): string[] {
  if (step.kind !== "enforced") return [];
  const w: string[] = [];
  const conditions = [...step.preconditions, ...step.postconditions].filter((c) => c.expr.trim());
  if (conditions.length === 0) {
    w.push("no verifiable condition — add a pre- or postcondition");
  }
  const tool = step.executeTool.trim();
  if (tool) {
    if (step.tools.length === 0) {
      w.push("executes a tool but has no tool allowlist");
    } else if (!step.tools.includes(tool)) {
      w.push(`executes '${tool}' which is missing from its allowlist [${step.tools.join(", ")}]`);
    }
    if (step.postconditions.filter((c) => c.expr.trim()).length === 0) {
      w.push("acts on the system of record but has no postcondition to verify the outcome");
    }
  }
  for (const c of conditions) {
    const m = /^\s*(\w+)\./.exec(c.expr);
    if (!m) {
      w.push(`check '${c.expr}' is not a binding.field expression`);
    } else if (!declaredBindings.includes(m[1])) {
      w.push(`check '${c.expr}' reads binding '${m[1]}' which is not declared in state`);
    }
  }
  return w;
}

function CheckRows({
  label,
  rows,
  onChange,
}: {
  label: string;
  rows: CheckRow[];
  onChange: (rows: CheckRow[]) => void;
}) {
  const update = (key: string, patch: Partial<CheckRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  return (
    <div>
      <div className="mb-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.key} className="grid gap-2 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto]">
            <input
              value={row.expr}
              onChange={(e) => update(row.key, { expr: e.target.value })}
              placeholder="binding.field == true"
              className={`${inputCls} font-mono text-xs`}
            />
            <input
              value={row.reason}
              onChange={(e) => update(row.key, { reason: e.target.value })}
              placeholder="else: reason shown when it fails"
              className={inputCls}
            />
            <button
              type="button"
              onClick={() => onChange(rows.filter((r) => r.key !== row.key))}
              className="font-mono text-xs text-muted-foreground hover:text-danger"
            >
              remove
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange([...rows, { key: newRowKey(), expr: "", reason: "" }])}
          className="font-mono text-xs text-primary hover:underline"
        >
          + add check
        </button>
      </div>
    </div>
  );
}

export function StepForm({
  step,
  index,
  count,
  declaredBindings,
  onChange,
  onMove,
  onRemove,
}: {
  step: EditableStep;
  index: number;
  count: number;
  declaredBindings: string[];
  onChange: (step: EditableStep) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [approvalOpen, setApprovalOpen] = useState(
    Boolean(step.approvalRequiredWhen || step.approvalPrompt),
  );
  const set = (patch: Partial<EditableStep>) => onChange({ ...step, ...patch });
  const trace = step.kind === "trace";
  const warnings = gateWarnings(step, declaredBindings);

  return (
    <div
      className={`rounded-xl border bg-surface ${trace ? "border-dashed border-border opacity-75" : "border-border"}`}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <span className="font-mono text-[11px] text-muted-foreground">#{index + 1}</span>
        <div className="flex overflow-hidden rounded-md border border-border" role="group">
          {(["trace", "enforced"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => set({ kind })}
              className={`px-2.5 py-1 font-mono text-[11px] ${
                step.kind === kind
                  ? kind === "enforced"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-ink"
                  : "bg-background text-muted-foreground hover:text-ink"
              }`}
            >
              {kind}
            </button>
          ))}
        </div>
        {trace && (
          <span className="font-mono text-[11px] text-muted-foreground">
            surface marker — never reaches the engine
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            aria-label="move step up"
            className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-ink disabled:opacity-30"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={index === count - 1}
            aria-label="move step down"
            className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-ink disabled:opacity-30"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={onRemove}
            className="rounded-md border border-border px-2 py-1 font-mono text-xs text-muted-foreground hover:text-danger"
          >
            remove
          </button>
        </div>
      </div>

      <div className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <Field label="step id">
            <input
              value={step.id}
              onChange={(e) => set({ id: e.target.value })}
              placeholder="verify_patient"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="description">
            <input
              value={step.description}
              onChange={(e) => set({ description: e.target.value })}
              placeholder="what this step does"
              className={inputCls}
            />
          </Field>
        </div>

        {!trace && (
          <>
            <Field label="tool allowlist">
              <TagInput
                value={step.tools}
                onChange={(tools) => set({ tools })}
                placeholder="book_appointment + Enter"
              />
            </Field>

            <CheckRows
              label="preconditions"
              rows={step.preconditions}
              onChange={(preconditions) => set({ preconditions })}
            />
            <CheckRows
              label="postconditions"
              rows={step.postconditions}
              onChange={(postconditions) => set({ postconditions })}
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="execute · adapter">
                <select
                  value={step.executeAdapter}
                  onChange={(e) => set({ executeAdapter: e.target.value })}
                  className={inputCls}
                >
                  <option value="registry">registry</option>
                  <option value="stub">stub</option>
                </select>
              </Field>
              <Field label="execute · tool">
                <input
                  value={step.executeTool}
                  onChange={(e) => set({ executeTool: e.target.value })}
                  placeholder="empty = check-only step"
                  className={`${inputCls} font-mono`}
                />
              </Field>
            </div>

            <div className="rounded-lg border border-border bg-background/60">
              <button
                type="button"
                onClick={() => setApprovalOpen((o) => !o)}
                className="flex w-full items-center justify-between px-3 py-2 text-left"
              >
                <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                  approval gate
                </span>
                <span className="font-mono text-xs text-muted-foreground">
                  {approvalOpen ? "−" : step.approvalRequiredWhen || step.approvalPrompt ? "configured +" : "+"}
                </span>
              </button>
              {approvalOpen && (
                <div className="grid gap-3 border-t border-border p-3 sm:grid-cols-2">
                  <Field label="required when">
                    <input
                      value={step.approvalRequiredWhen}
                      onChange={(e) => set({ approvalRequiredWhen: e.target.value })}
                      placeholder="binding.field == true (empty = never)"
                      className={`${inputCls} font-mono text-xs`}
                    />
                  </Field>
                  <Field label="prompt">
                    <input
                      value={step.approvalPrompt}
                      onChange={(e) => set({ approvalPrompt: e.target.value })}
                      placeholder="what the approver is asked"
                      className={inputCls}
                    />
                  </Field>
                </div>
              )}
            </div>
          </>
        )}

        {warnings.length > 0 && (
          <div className="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2">
            <div className="font-mono text-[11px] uppercase tracking-widest text-warning">
              publish gate will refuse
            </div>
            <ul className="mt-1.5 space-y-1">
              {warnings.map((w, i) => (
                <li key={i} className="flex gap-2 text-sm text-foreground">
                  <span className="shrink-0 text-warning" aria-hidden>
                    ⊘
                  </span>
                  <span className="font-mono text-[13px]">{w}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}