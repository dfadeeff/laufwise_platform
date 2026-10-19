"use client";
import type { FieldSchema } from "@/types";

// Shows a computed result through the same schema dialect as the form (ADR-0030): titles come
// from the module, so a property manager reads "Saldo", not "balance". A list of flat records is
// a table; a list of records that hold lists is one card per record. Read-only by construction:
// a result is changed by correcting the input and computing again.

type Value = unknown;
type Rec = Record<string, Value>;

const isComposite = (s: FieldSchema) => s.type === "object" || s.type === "array";

function show(schema: FieldSchema, value: Value): string {
  if (value === null || value === undefined || value === "") return "—";
  if (schema.enum) return schema.enum.find((o) => o.value === value)?.label ?? String(value);
  if (schema.type === "boolean") return value ? "yes" : "no";
  if (schema.format === "date") {
    const [y, m, d] = String(value).split("-");
    return d ? `${d}.${m}.${y}` : String(value);
  }
  return String(value);
}

function Scalars({ schema, value }: { schema: FieldSchema; value: Rec }) {
  const entries = Object.entries(schema.properties ?? {}).filter(([, p]) => !isComposite(p));
  if (entries.length === 0) return null;
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
      {entries.map(([key, p]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{p.title ?? key}</dt>
          <dd
            className={`mt-0.5 text-sm text-ink ${p.format === "decimal" ? "tabular-nums" : ""} ${String(value[key] ?? "").length > 60 ? "leading-6" : "font-medium"}`}
          >
            {show(p, value[key])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Table({ item, rows }: { item: FieldSchema; rows: Rec[] }) {
  const columns = Object.entries(item.properties ?? {});
  return (
    <>
      {/* A phone gets one block per row, so no figure hides behind a sideways scroll. */}
      <ul className="divide-y divide-border rounded-xl border border-border bg-white sm:hidden">
        {rows.map((row, i) => (
          <li key={i} className="grid grid-cols-2 gap-x-3 gap-y-1.5 px-3 py-3 text-[13px]">
            {columns.map(([key, p]) => (
              <div
                key={key}
                className={String(row[key] ?? "").length > 24 ? "col-span-2" : ""}
              >
                <span className="block text-[11px] text-muted-foreground">{p.title ?? key}</span>
                <span className={`text-ink ${p.format === "decimal" ? "tabular-nums" : ""}`}>
                  {show(p, row[key])}
                </span>
              </div>
            ))}
          </li>
        ))}
      </ul>
    <div className="hidden overflow-x-auto rounded-xl border border-border bg-white sm:block">
      <table className="w-full min-w-[640px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-border">
            {columns.map(([key, p]) => (
              <th
                key={key}
                scope="col"
                className={`studio-eyebrow px-3 py-2 font-semibold ${p.format === "decimal" ? "text-right" : ""}`}
              >
                {p.title ?? key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-border align-top last:border-0">
              {columns.map(([key, p]) => (
                <td
                  key={key}
                  // A short value (a number, "181/365 Tage") never breaks; prose wraps.
                  className={`px-3 py-2 text-ink ${p.format === "decimal" ? "whitespace-nowrap text-right tabular-nums" : String(row[key] ?? "").length <= 16 ? "whitespace-nowrap" : ""}`}
                >
                  {show(p, row[key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </>
  );
}

function Section({ schema, value }: { schema: FieldSchema; value: Value }) {
  if (schema.type === "array") {
    const item = schema.items!;
    const rows = (value as Value[]) ?? [];
    return (
      <section>
        <h3 className="text-[15px] font-semibold text-ink">{schema.title}</h3>
        <div className="mt-3">
          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">None.</p>
          ) : item.type !== "object" ? (
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink">
              {rows.map((r, i) => (
                <li key={i}>{show(item, r)}</li>
              ))}
            </ul>
          ) : Object.values(item.properties ?? {}).some(isComposite) ? (
            <div className="space-y-4">
              {rows.map((r, i) => (
                <div key={i} className="studio-section space-y-5 p-5 sm:p-5">
                  <Body schema={item} value={r as Rec} />
                </div>
              ))}
            </div>
          ) : (
            <Table item={item} rows={rows as Rec[]} />
          )}
        </div>
      </section>
    );
  }
  return (
    <section>
      <h3 className="text-[15px] font-semibold text-ink">{schema.title}</h3>
      <div className="mt-3">
        {value ? <Body schema={schema} value={value as Rec} /> : <p className="text-sm text-muted-foreground">—</p>}
      </div>
    </section>
  );
}

function Body({ schema, value }: { schema: FieldSchema; value: Rec }) {
  const composites = Object.entries(schema.properties ?? {}).filter(([, p]) => isComposite(p));
  return (
    <div className="space-y-6">
      <Scalars schema={schema} value={value} />
      {composites.map(([key, p]) => (
        <Section key={key} schema={p} value={value[key]} />
      ))}
    </div>
  );
}

export function ResultView({ schema, value }: { schema: FieldSchema; value: Rec }) {
  return <Body schema={schema} value={value} />;
}
