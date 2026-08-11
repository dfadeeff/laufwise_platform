"use client";

// Small shared Studio primitives — badges, form fields, the tag input, and the
// violations panel that renders publish/deploy gate messages verbatim.

import { useState, type ReactNode } from "react";

export const inputCls =
  "w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm text-ink placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-primary/40";

export function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className ?? ""}`}>
      <span className="mb-1 block font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
      {children}
    </div>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const cls =
    risk === "high"
      ? "border-danger/20 bg-danger/10 text-danger"
      : risk === "low"
        ? "border-success/20 bg-success/10 text-success"
        : "border-warning/20 bg-warning/10 text-warning";
  return (
    <span className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${cls}`}>
      {risk} risk
    </span>
  );
}

const STATUS_CHIP: Record<string, string> = {
  published: "border-success/20 bg-success/10 text-success",
  deployed: "border-success/20 bg-success/10 text-success",
  paused: "border-warning/20 bg-warning/10 text-warning",
  draft: "border-border bg-muted text-muted-foreground",
};

export function StatusChip({ status }: { status: string }) {
  return (
    <span
      className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${STATUS_CHIP[status] ?? STATUS_CHIP.draft}`}
    >
      {status}
    </span>
  );
}

export function TagInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const add = () => {
    const t = text.trim().replace(/,+$/, "");
    if (t && !value.includes(t)) onChange([...value, t]);
    setText("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground"
        >
          {tag}
          <button
            type="button"
            onClick={() => onChange(value.filter((t) => t !== tag))}
            className="text-muted-foreground hover:text-danger"
            aria-label={`remove ${tag}`}
          >
            ×
          </button>
        </span>
      ))}
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
        placeholder={placeholder ?? "add + Enter"}
        className="min-w-24 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted-foreground/60"
      />
    </div>
  );
}

/** The gate's messages are the product — each violation rendered verbatim, in red. */
export function ViolationsPanel({ title, violations }: { title: string; violations: string[] }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger/5 p-4">
      <div className="font-mono text-[11px] uppercase tracking-widest text-danger">{title}</div>
      <ul className="mt-2 space-y-1.5">
        {violations.map((v, i) => (
          <li key={i} className="flex gap-2 text-sm text-foreground">
            <span className="shrink-0 text-danger" aria-hidden>
              ✕
            </span>
            <span className="font-mono text-[13px]">{v}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Notice({
  tone,
  children,
}: {
  // `warning` = it worked, but a human has to look at it — distinct from `error` (it failed) and
  // from `info` (nothing to do). Used for forced imports (ADR-0005 D7).
  tone: "success" | "error" | "warning" | "info";
  children: ReactNode;
}) {
  const cls =
    tone === "success"
      ? "border-success/30 bg-success/5"
      : tone === "error"
        ? "border-danger/30 bg-danger/5"
        : tone === "warning"
          ? "border-warning/40 bg-warning/5"
          : "border-border bg-muted/50";
  return <div className={`rounded-lg border px-4 py-3 text-sm text-foreground ${cls}`}>{children}</div>;
}