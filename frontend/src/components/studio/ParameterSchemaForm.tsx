"use client";

// Edits the contract's `parameters` dict (ADR-0002 #9) as rows: key, type, enum
// options, default, required, label. The configuration tier auto-renders its form
// from exactly this schema.

import { inputCls } from "@/components/studio/ui";
import type { ParameterSpec, ParameterType } from "@/types";

export interface ParamRow {
  key: string; // client-only list key
  name: string;
  type: ParameterType;
  optionsText: string; // comma-separated enum options
  defaultText: string;
  required: boolean;
  label: string;
}

let seq = 0;
export const newRowKey = () => `k${Date.now().toString(36)}-${++seq}`;

export function parametersToRows(parameters: Record<string, ParameterSpec>): ParamRow[] {
  return Object.entries(parameters).map(([name, spec]) => ({
    key: newRowKey(),
    name,
    type: spec.type ?? "text",
    optionsText: (spec.options ?? []).map(String).join(", "),
    defaultText: spec.default === null || spec.default === undefined ? "" : String(spec.default),
    required: spec.required ?? false,
    label: spec.label ?? "",
  }));
}

function parseDefault(type: ParameterType, text: string): unknown {
  const t = text.trim();
  if (t === "") return null;
  if (type === "int") {
    const n = Number(t);
    return Number.isNaN(n) ? t : n;
  }
  if (type === "bool") return t === "true";
  return t;
}

export function rowsToParameters(rows: ParamRow[]): Record<string, ParameterSpec> {
  const out: Record<string, ParameterSpec> = {};
  for (const row of rows) {
    const name = row.name.trim();
    if (!name) continue;
    out[name] = {
      type: row.type,
      options:
        row.type === "enum"
          ? row.optionsText
              .split(",")
              .map((o) => o.trim())
              .filter(Boolean)
          : null,
      default: parseDefault(row.type, row.defaultText),
      required: row.required,
      label: row.label.trim() || null,
    };
  }
  return out;
}

const TYPES: ParameterType[] = ["text", "enum", "bool", "int"];

export function ParameterSchemaForm({
  rows,
  onChange,
}: {
  rows: ParamRow[];
  onChange: (rows: ParamRow[]) => void;
}) {
  const update = (key: string, patch: Partial<ParamRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <div className="space-y-3">
      {rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No parameters yet — each one becomes a field on the configuration form.
        </p>
      )}
      {rows.map((row) => (
        <div key={row.key} className="rounded-lg border border-border bg-background/60 p-3">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_minmax(0,1fr)]">
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] text-muted-foreground">key</span>
              <input
                value={row.name}
                onChange={(e) => update(row.key, { name: e.target.value })}
                placeholder="practice_name"
                className={`${inputCls} font-mono`}
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] text-muted-foreground">type</span>
              <select
                value={row.type}
                onChange={(e) => update(row.key, { type: e.target.value as ParameterType })}
                className={inputCls}
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] text-muted-foreground">label</span>
              <input
                value={row.label}
                onChange={(e) => update(row.key, { label: e.target.value })}
                placeholder="shown on the config form"
                className={inputCls}
              />
            </label>
          </div>
          <div className="mt-3 grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto]">
            {row.type === "enum" ? (
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] text-muted-foreground">
                  options (comma-separated)
                </span>
                <input
                  value={row.optionsText}
                  onChange={(e) => update(row.key, { optionsText: e.target.value })}
                  placeholder="morning, afternoon, any"
                  className={`${inputCls} font-mono`}
                />
              </label>
            ) : (
              <div className="hidden sm:block" />
            )}
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] text-muted-foreground">default</span>
              <input
                value={row.defaultText}
                onChange={(e) => update(row.key, { defaultText: e.target.value })}
                placeholder={row.type === "bool" ? "true | false" : "optional"}
                className={`${inputCls} font-mono`}
              />
            </label>
            <label className="flex items-center gap-2 pb-1.5 text-sm text-foreground">
              <input
                type="checkbox"
                checked={row.required}
                onChange={(e) => update(row.key, { required: e.target.checked })}
                className="h-4 w-4 accent-[oklch(0.55_0.17_255)]"
              />
              required
            </label>
            <button
              type="button"
              onClick={() => onChange(rows.filter((r) => r.key !== row.key))}
              className="pb-1 text-left font-mono text-xs text-muted-foreground hover:text-danger"
            >
              remove
            </button>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange([
            ...rows,
            {
              key: newRowKey(),
              name: "",
              type: "text",
              optionsText: "",
              defaultText: "",
              required: false,
              label: "",
            },
          ])
        }
        className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-foreground hover:bg-muted"
      >
        + Add parameter
      </button>
    </div>
  );
}