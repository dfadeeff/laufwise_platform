"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { StudioHeader } from "@/components/studio/StudioHeader";
import {
  ParameterSchemaForm,
  newRowKey,
  parametersToRows,
  rowsToParameters,
  type ParamRow,
} from "@/components/studio/ParameterSchemaForm";
import {
  StepForm,
  blankStep,
  editableToStep,
  stepToEditable,
  type EditableStep,
} from "@/components/studio/StepForm";
import {
  Field,
  Notice,
  SectionTitle,
  TagInput,
  ViolationsPanel,
  inputCls,
} from "@/components/studio/ui";
import type { AgentClass, PublishResult, TemplateContract } from "@/types";

// The authoring tier: a structured step-form editor over the TemplateContract
// (ADR-0002 — ordered step forms, not a graph, not raw YAML). Drafts are
// workbenches; the publish gate is the wall.

interface BindingRow {
  key: string;
  name: string;
  provider: string;
}

export default function AuthorPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-background" />}>
      <AuthorEditor />
    </Suspense>
  );
}

function AuthorEditor() {
  const searchParams = useSearchParams();
  const loadName = searchParams.get("template");

  // Top-level contract fields.
  const [name, setName] = useState("");
  const [risk, setRisk] = useState("medium");
  const [agentClass, setAgentClass] = useState<AgentClass>("conversational");
  const [agentSurface, setAgentSurface] = useState("");
  const [connections, setConnections] = useState<string[]>([]);
  const [bindings, setBindings] = useState<BindingRow[]>([]);
  const [params, setParams] = useState<ParamRow[]>([]);
  const [steps, setSteps] = useState<EditableStep[]>([blankStep()]);

  // Load / save lifecycle.
  const [loading, setLoading] = useState(Boolean(loadName));
  const [busy, setBusy] = useState<"save" | "publish" | null>(null);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [published, setPublished] = useState<PublishResult | null>(null);
  const [violations, setViolations] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!loadName) return;
    let cancelled = false;
    (async () => {
      try {
        // Latest version regardless of status: editing a published version forks
        // the next draft server-side on save.
        const detail = await api.getTemplate(loadName);
        if (cancelled) return;
        const c = detail.contract;
        setName(c.name);
        setRisk(c.risk ?? "medium");
        setAgentClass(c.agent_class ?? "conversational");
        setAgentSurface(c.agent_surface ?? "");
        setConnections(c.required_connections ?? []);
        setBindings(
          Object.entries(c.state ?? {}).map(([bindingName, def]) => ({
            key: newRowKey(),
            name: bindingName,
            provider: def.provider ?? "memory",
          })),
        );
        setParams(parametersToRows(c.parameters ?? {}));
        setSteps((c.steps ?? []).map(stepToEditable));
      } catch (e) {
        if (!cancelled) setError(`could not load template '${loadName}': ${e instanceof Error ? e.message : e}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadName]);

  const declaredBindings = bindings.map((b) => b.name.trim()).filter(Boolean);

  const toContract = (): TemplateContract => ({
    name: name.trim(),
    risk,
    agent_class: agentClass,
    agent_surface: agentSurface.trim() || null,
    parameters: rowsToParameters(params),
    required_connections: connections,
    state: Object.fromEntries(
      bindings
        .filter((b) => b.name.trim())
        .map((b) => [b.name.trim(), { provider: b.provider.trim() || "memory" }]),
    ),
    steps: steps.map(editableToStep),
  });

  const clearNotices = () => {
    setSavedVersion(null);
    setPublished(null);
    setViolations(null);
    setError(null);
  };

  const saveDraft = async (): Promise<string | null> => {
    clearNotices();
    if (!name.trim()) {
      setError("the template needs a name before it can be saved");
      return null;
    }
    try {
      const detail = await api.saveDraft(toContract());
      setSavedVersion(detail.version);
      return detail.name;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  };

  const onSave = async () => {
    setBusy("save");
    await saveDraft();
    setBusy(null);
  };

  const onPublish = async () => {
    setBusy("publish");
    // Publish always gates the *current* form state: save the draft first.
    const savedName = await saveDraft();
    if (savedName) {
      try {
        setPublished(await api.publishTemplate(savedName));
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && e.violations.length > 0) {
          setViolations(e.violations);
        } else {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    }
    setBusy(null);
  };

  const updateBinding = (key: string, patch: Partial<BindingRow>) =>
    setBindings((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const moveStep = (index: number, dir: -1 | 1) =>
    setSteps((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });

  return (
    <div className="min-h-screen bg-background">
      <StudioHeader active="studio" />
      <main className="mx-auto max-w-5xl px-5 py-8 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">Author a template</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Ordered step forms compile 1:1 to the governed contract. Drafts save freely; the
              publish gate refuses anything ungoverned.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={busy !== null || loading}
              className="rounded-md border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              {busy === "save" ? "Saving…" : "Save draft"}
            </button>
            <button
              type="button"
              onClick={() => void onPublish()}
              disabled={busy !== null || loading}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {busy === "publish" ? "Publishing…" : "Publish"}
            </button>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {error && <Notice tone="error">{error}</Notice>}
          {savedVersion !== null && !published && (
            <Notice tone="success">
              Draft saved as <span className="font-mono">{name.trim()} v{savedVersion}</span>.
            </Notice>
          )}
          {published && (
            <Notice tone="success">
              Published <span className="font-mono">{published.name} v{published.version}</span> —
              immutable from here on; editing forks the next draft.{" "}
              <Link
                href={`/studio/configure/${encodeURIComponent(published.name)}`}
                className="font-mono text-primary hover:underline"
              >
                configure & deploy →
              </Link>
            </Notice>
          )}
          {violations && <ViolationsPanel title="publish refused by the governance gate" violations={violations} />}
        </div>

        {loading ? (
          <p className="mt-6 font-mono text-xs text-muted-foreground">loading template…</p>
        ) : (
          <div className="mt-6 space-y-6">
            <section className="rounded-xl border border-border bg-surface p-5">
              <SectionTitle>Template</SectionTitle>
              <div className="mt-4 grid gap-4 sm:grid-cols-3">
                <Field label="name">
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="praxis_appointment"
                    className={`${inputCls} font-mono`}
                  />
                </Field>
                <Field label="risk">
                  <select value={risk} onChange={(e) => setRisk(e.target.value)} className={inputCls}>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                </Field>
                <Field label="agent class">
                  <select
                    value={agentClass}
                    onChange={(e) => setAgentClass(e.target.value as AgentClass)}
                    className={inputCls}
                  >
                    <option value="conversational">conversational</option>
                    <option value="workflow">workflow</option>
                  </select>
                </Field>
              </div>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="agent surface (optional)">
                  <input
                    value={agentSurface}
                    onChange={(e) => setAgentSurface(e.target.value)}
                    placeholder="voice"
                    className={inputCls}
                  />
                </Field>
                <Field label="required connections">
                  <TagInput
                    value={connections}
                    onChange={setConnections}
                    placeholder="calendar + Enter"
                  />
                </Field>
              </div>

              <div className="mt-5">
                <SectionTitle>State bindings</SectionTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Checks may only read through these — an undeclared binding fails the gate.
                </p>
                <div className="mt-3 space-y-2">
                  {bindings.map((row) => (
                    <div
                      key={row.key}
                      className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                    >
                      <input
                        value={row.name}
                        onChange={(e) => updateBinding(row.key, { name: e.target.value })}
                        placeholder="binding name (e.g. consent)"
                        className={`${inputCls} font-mono`}
                      />
                      <input
                        value={row.provider}
                        onChange={(e) => updateBinding(row.key, { provider: e.target.value })}
                        placeholder="provider (memory)"
                        className={`${inputCls} font-mono`}
                      />
                      <button
                        type="button"
                        onClick={() => setBindings((rows) => rows.filter((r) => r.key !== row.key))}
                        className="font-mono text-xs text-muted-foreground hover:text-danger"
                      >
                        remove
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() =>
                      setBindings((rows) => [...rows, { key: newRowKey(), name: "", provider: "memory" }])
                    }
                    className="font-mono text-xs text-primary hover:underline"
                  >
                    + add binding
                  </button>
                </div>
              </div>
            </section>

            <section className="rounded-xl border border-border bg-surface p-5">
              <SectionTitle>Parameters</SectionTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                The configuration form is auto-rendered from this schema.
              </p>
              <div className="mt-4">
                <ParameterSchemaForm rows={params} onChange={setParams} />
              </div>
            </section>

            <section>
              <SectionTitle>Steps</SectionTitle>
              <div className="mt-3 space-y-4">
                {steps.map((step, i) => (
                  <StepForm
                    key={step.key}
                    step={step}
                    index={i}
                    count={steps.length}
                    declaredBindings={declaredBindings}
                    onChange={(updated) =>
                      setSteps((prev) => prev.map((s) => (s.key === step.key ? updated : s)))
                    }
                    onMove={(dir) => moveStep(i, dir)}
                    onRemove={() => setSteps((prev) => prev.filter((s) => s.key !== step.key))}
                  />
                ))}
                <button
                  type="button"
                  onClick={() => setSteps((prev) => [...prev, blankStep()])}
                  className="w-full rounded-xl border border-dashed border-border bg-surface px-4 py-3 text-sm text-muted-foreground hover:border-primary/40 hover:text-ink"
                >
                  + Add step
                </button>
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}