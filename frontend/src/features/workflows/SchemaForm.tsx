"use client";
import type { FieldSchema } from "@/types";
import { Icon } from "@/components/studio/icons";

// Renders any form the kernel derives from a module's dataclasses (ADR-0030), completely: the
// dialect is small on purpose, so no module ever needs a hand-built editor for its input.
// Scalars sit in a responsive grid; lists and nested objects take the full width below them.

type Value = unknown;

const isComposite = (s: FieldSchema) => s.type === "object" || s.type === "array";

/** The blank value a new item starts from, as the backend's `empty()` makes it. */
export function blank(schema: FieldSchema): Value {
  if (schema.default !== undefined) return schema.default;
  if (schema.nullable) return null;
  if (schema.type === "object")
    return Object.fromEntries(
      Object.entries(schema.properties ?? {}).map(([k, p]) => [k, blank(p)]),
    );
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "integer") return 0;
  return "";
}

const inputClass =
  "h-9 w-full rounded-lg border border-border bg-white px-3 text-[13px] text-ink outline-none transition placeholder:text-faint focus:border-primary focus:ring-2 focus:ring-primary/15";

function Scalar({
  schema,
  value,
  onChange,
  label,
}: {
  schema: FieldSchema;
  value: Value;
  onChange: (v: Value) => void;
  label: string;
}) {
  // A nullable field that is cleared is null, not "": "not given" and "given as empty" differ.
  const text = (v: string) => onChange(v === "" && schema.nullable ? null : v);
  if (schema.type === "boolean")
    return (
      <label className="flex h-9 items-center gap-2 text-[13px] text-ink">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-primary"
        />
        {label}
      </label>
    );
  if (schema.enum)
    return (
      <select
        aria-label={label}
        value={(value as string) ?? ""}
        onChange={(e) => text(e.target.value)}
        className={inputClass}
      >
        <option value="">—</option>
        {schema.enum.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  if (schema.type === "integer")
    return (
      <input
        aria-label={label}
        type="number"
        value={(value as number) ?? 0}
        onChange={(e) => onChange(Number(e.target.value))}
        className={inputClass}
      />
    );
  return (
    <input
      aria-label={label}
      type={schema.format === "date" ? "date" : "text"}
      inputMode={schema.format === "decimal" ? "decimal" : undefined}
      placeholder={schema.format === "decimal" ? "0.00" : undefined}
      value={(value as string) ?? ""}
      onChange={(e) =>
        text(schema.format === "decimal" ? e.target.value.replace(",", ".") : e.target.value)
      }
      className={`${inputClass} ${schema.format === "decimal" ? "text-right tabular-nums" : ""}`}
    />
  );
}

function ObjectFields({
  schema,
  value,
  onChange,
}: {
  schema: FieldSchema;
  value: Record<string, Value>;
  onChange: (v: Record<string, Value>) => void;
}) {
  const entries = Object.entries(schema.properties ?? {});
  const set = (key: string, v: Value) => onChange({ ...value, [key]: v });
  const scalars = entries.filter(([, p]) => !isComposite(p));
  const composites = entries.filter(([, p]) => isComposite(p));
  return (
    <div className="space-y-5">
      {scalars.length > 0 && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {scalars.map(([key, p]) => (
            <div key={key} className="min-w-0">
              {p.type !== "boolean" && (
                <span className="mb-1 block text-xs font-medium text-muted-foreground">
                  {p.title ?? key}
                </span>
              )}
              <Scalar
                schema={p}
                value={value?.[key]}
                onChange={(v) => set(key, v)}
                label={p.title ?? key}
              />
            </div>
          ))}
        </div>
      )}
      {composites.map(([key, p]) => (
        <Field key={key} schema={p} value={value?.[key]} onChange={(v) => set(key, v)} />
      ))}
    </div>
  );
}

function Field({
  schema,
  value,
  onChange,
}: {
  schema: FieldSchema;
  value: Value;
  onChange: (v: Value) => void;
}) {
  const title = schema.title ?? "";
  if (schema.type === "object") {
    if (value === null || value === undefined)
      return (
        <div>
          <p className="text-[13px] font-semibold text-ink">{title}</p>
          <button
            type="button"
            className="studio-secondary mt-2"
            onClick={() => onChange(blank({ ...schema, nullable: false }))}
          >
            <Icon name="plus" size={14} width={2.2} />
            Add
          </button>
        </div>
      );
    return (
      <fieldset className="rounded-xl border border-border p-4">
        <legend className="flex items-center gap-3 px-1 text-[13px] font-semibold text-ink">
          {title}
          {schema.nullable && (
            <button
              type="button"
              className="text-xs font-normal text-muted-foreground underline"
              onClick={() => onChange(null)}
            >
              Remove
            </button>
          )}
        </legend>
        <ObjectFields
          schema={schema}
          value={value as Record<string, Value>}
          onChange={onChange}
        />
      </fieldset>
    );
  }
  if (schema.type === "array") {
    const items = (value as Value[]) ?? [];
    const item = schema.items!;
    const setAt = (i: number, v: Value) => onChange(items.map((x, j) => (j === i ? v : x)));
    const removeAt = (i: number) => onChange(items.filter((_, j) => j !== i));
    return (
      <div>
        <p className="text-[13px] font-semibold text-ink">
          {title}
          <span className="ml-2 font-normal text-muted-foreground">{items.length}</span>
        </p>
        <div className="mt-2 space-y-3">
          {items.map((v, i) =>
            item.type === "object" ? (
              <div key={i} className="rounded-xl border border-border bg-surface/60 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="font-mono text-[11px] text-faint">#{i + 1}</span>
                  <button
                    type="button"
                    className="text-xs text-muted-foreground underline"
                    onClick={() => removeAt(i)}
                  >
                    Remove
                  </button>
                </div>
                <ObjectFields
                  schema={item}
                  value={v as Record<string, Value>}
                  onChange={(nv) => setAt(i, nv)}
                />
              </div>
            ) : (
              <div key={i} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <Scalar schema={item} value={v} onChange={(nv) => setAt(i, nv)} label={title} />
                </div>
                <button
                  type="button"
                  className="text-xs text-muted-foreground underline"
                  onClick={() => removeAt(i)}
                >
                  Remove
                </button>
              </div>
            ),
          )}
        </div>
        <button
          type="button"
          className="studio-secondary mt-3"
          onClick={() => onChange([...items, blank(item)])}
        >
          <Icon name="plus" size={14} width={2.2} />
          Add
        </button>
      </div>
    );
  }
  return null;
}

/** The whole form for one object schema. `value` is replaced, never mutated. */
export function SchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: FieldSchema;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}) {
  return <ObjectFields schema={schema} value={value} onChange={onChange} />;
}
