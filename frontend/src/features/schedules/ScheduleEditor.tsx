"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SchemaForm, blank } from "@/features/workflows/SchemaForm";
import type { FieldSchema, Schedule, ScheduleRule } from "@/types";
import { DAYS, INTERVALS, SLOTS, describeRule, when } from "./rules";

// Creating or changing a schedule (ADR-0040 D4): a name, when it runs (a few times a day, or
// every so often within opening hours, always on the 30-minute grid), and the input each run
// starts from. The next five times come from the server, so what is previewed is what runs.

const TIMEZONE = "Europe/Berlin";
const select =
  "h-9 rounded-lg border border-border bg-white px-2.5 text-[13px] text-ink outline-none focus:border-primary focus:ring-2 focus:ring-primary/15";
const label = "mb-1.5 block text-[12px] font-medium text-muted-foreground";

export interface ScheduleDraft {
  name: string;
  rule: ScheduleRule;
  input: Record<string, unknown>;
}

const FIRST: ScheduleRule = { kind: "times", at: ["08:00", "13:00", "18:00"], days: [1, 2, 3, 4, 5] };

function Days({ value, onChange }: { value: number[]; onChange: (d: number[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Days">
      {DAYS.map((d) => {
        const on = value.includes(d.n);
        return (
          <button
            key={d.n}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== d.n) : [...value, d.n].sort())}
            className={`h-8 min-w-[44px] rounded-lg border px-2 text-[12px] font-medium ${
              on ? "border-primary bg-primary text-white" : "border-border bg-white text-ink hover:bg-muted"
            }`}
          >
            {d.short}
          </button>
        );
      })}
    </div>
  );
}

export function ScheduleEditor({
  schema,
  initial,
  busy,
  onSave,
  onCancel,
}: {
  schema: FieldSchema | null;
  initial: Schedule | null;
  busy: boolean;
  onSave: (draft: ScheduleDraft) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [rule, setRule] = useState<ScheduleRule>(initial?.rule ?? FIRST);
  const [input, setInput] = useState<Record<string, unknown>>(
    initial?.input ?? ((schema ? blank(schema) : {}) as Record<string, unknown>),
  );
  const [preview, setPreview] = useState<{ times: string[]; error: string }>({ times: [], error: "" });

  const days = rule.days ?? [1, 2, 3, 4, 5, 6, 7];
  const ruleKey = JSON.stringify(rule);
  useEffect(() => {
    const t = setTimeout(() => {
      api
        .previewSchedule(rule, initial?.timezone ?? TIMEZONE)
        .then((r) => setPreview({ times: r.times, error: "" }))
        .catch((e) => setPreview({ times: [], error: e instanceof Error ? e.message : String(e) }));
    }, 250);
    return () => clearTimeout(t);
  }, [ruleKey]); // the rule's content, not its identity


  const setTimes = (at: string[]) => rule.kind === "times" && setRule({ ...rule, at });
  const valid = name.trim() !== "" && days.length > 0 && !preview.error && (rule.kind !== "times" || rule.at.length > 0);

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !busy) onSave({ name: name.trim(), rule: { ...rule, days }, input });
      }}
    >
      <div>
        <label className={label} htmlFor="schedule-name">Name</label>
        <input
          id="schedule-name"
          value={name}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Morning, midday and evening run"
          className={`${select} w-full sm:max-w-md`}
        />
      </div>

      <fieldset>
        <legend className={label}>When it runs</legend>
        <div role="radiogroup" className="inline-flex rounded-lg border border-border bg-white p-0.5 text-[13px]">
          {(
            [
              ["times", "At set times"],
              ["every", "Every so often"],
            ] as const
          ).map(([kind, text]) => (
            <button
              key={kind}
              type="button"
              role="radio"
              aria-checked={rule.kind === kind}
              onClick={() =>
                rule.kind !== kind &&
                setRule(
                  kind === "times"
                    ? { kind, at: ["08:00", "13:00", "18:00"], days }
                    : { kind, minutes: 120, from: "08:00", to: "18:00", days },
                )
              }
              className={`rounded-md px-3 py-1.5 ${rule.kind === kind ? "bg-primary text-white" : "text-ink"}`}
            >
              {text}
            </button>
          ))}
        </div>

        <div className="mt-4 space-y-4">
          <Days value={days} onChange={(d) => setRule({ ...rule, days: d })} />

          {rule.kind === "times" ? (
            <div className="flex flex-wrap items-center gap-2">
              {rule.at.map((t, i) => (
                <span key={`${t}-${i}`} className="inline-flex items-center gap-1">
                  <select
                    aria-label={`Time ${i + 1}`}
                    value={t}
                    onChange={(e) => setTimes(rule.at.map((x, j) => (j === i ? e.target.value : x)))}
                    className={select}
                  >
                    {SLOTS.map((s) => (
                      <option key={s} disabled={s !== t && rule.at.includes(s)}>{s}</option>
                    ))}
                  </select>
                  {rule.at.length > 1 && (
                    <button
                      type="button"
                      aria-label={`Remove ${t}`}
                      onClick={() => setTimes(rule.at.filter((_, j) => j !== i))}
                      className="h-9 rounded-lg px-2 text-[13px] text-muted-foreground hover:bg-muted"
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
              {rule.at.length < 12 && (
                <button
                  type="button"
                  onClick={() => setTimes([...rule.at, SLOTS.find((s) => !rule.at.includes(s)) ?? "12:00"])}
                  className="studio-secondary"
                >
                  Add a time
                </button>
              )}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink">
              <span>Every</span>
              <select
                aria-label="Interval"
                value={rule.minutes}
                onChange={(e) => setRule({ ...rule, minutes: Number(e.target.value) })}
                className={select}
              >
                {INTERVALS.map((i) => (
                  <option key={i.minutes} value={i.minutes}>{i.label}</option>
                ))}
              </select>
              <span>from</span>
              <select aria-label="From" value={rule.from ?? "00:00"} onChange={(e) => setRule({ ...rule, from: e.target.value })} className={select}>
                {SLOTS.map((s) => <option key={s}>{s}</option>)}
              </select>
              <span>to</span>
              <select aria-label="To" value={rule.to ?? "23:30"} onChange={(e) => setRule({ ...rule, to: e.target.value })} className={select}>
                {SLOTS.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
          )}
        </div>
      </fieldset>

      <div className="rounded-xl border border-border bg-surface p-4">
        <p className="text-sm font-medium text-ink">{describeRule({ ...rule, days })}</p>
        {preview.error ? (
          <p role="alert" className="mt-1 text-[13px] text-danger">{preview.error}</p>
        ) : (
          <>
            <p className="mt-1 text-[12px] text-muted-foreground">Next runs ({initial?.timezone ?? TIMEZONE}):</p>
            <ol className="mt-1.5 grid gap-x-4 gap-y-0.5 text-[13px] text-ink sm:grid-cols-2">
              {preview.times.map((t) => (
                <li key={t}>{when(t, initial?.timezone ?? TIMEZONE)}</li>
              ))}
            </ol>
          </>
        )}
      </div>

      {schema && (
        <div>
          <p className={label}>The input each run starts from</p>
          <p className="mb-3 text-[13px] text-muted-foreground">
            Every run creates a new case with this input and computes it. Anything the case would send still
            waits for a person&apos;s approval.
          </p>
          <SchemaForm schema={schema} value={input} onChange={setInput} />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="studio-primary" disabled={busy || !valid}>
          {initial ? "Save changes" : "Create schedule"}
        </button>
        <button type="button" className="studio-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}
