"use client";

// One auto-rendered configuration field (ADR-0002 #9): the ParameterSpec's `type`
// drives the widget — text -> input, enum -> select, bool -> toggle, int -> number.

import { Field, inputCls } from "@/components/studio/ui";
import type { ParameterSpec } from "@/types";

export function ParameterField({
  name,
  spec,
  value,
  onChange,
}: {
  name: string;
  spec: ParameterSpec;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const label = `${spec.label ?? name}${spec.required ? " *" : ""}`;

  if (spec.type === "bool") {
    return (
      <label className="flex items-center justify-between rounded-md border border-border bg-background/60 px-3 py-2">
        <span className="text-sm text-ink">{label}</span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-[oklch(0.55_0.17_255)]"
        />
      </label>
    );
  }

  if (spec.type === "enum") {
    const options = (spec.options ?? []).map(String);
    return (
      <Field label={label}>
        <select
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        >
          <option value="" disabled>
            select…
          </option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  if (spec.type === "int") {
    return (
      <Field label={label}>
        <input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) =>
            onChange(e.target.value === "" ? undefined : Number(e.target.value))
          }
          className={`${inputCls} font-mono`}
        />
      </Field>
    );
  }

  return (
    <Field label={label}>
      <input
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        placeholder={spec.required ? "required" : "optional"}
        className={inputCls}
      />
    </Field>
  );
}