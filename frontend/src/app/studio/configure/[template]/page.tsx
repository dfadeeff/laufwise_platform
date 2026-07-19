"use client";

// The configuration cockpit (Stage 4, ADR-0002 #3/#9): select a published template,
// fill the form auto-rendered from its `parameters` schema, bind connections
// (simulated in v1), deploy a governed AgentInstance — then test-run it and watch
// the enforced loop rule on the case.

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { ParameterField } from "@/components/studio/ParameterField";
import { StudioHeader } from "@/components/studio/StudioHeader";
import {
  Field,
  Notice,
  RiskBadge,
  SectionTitle,
  StatusChip,
  ViolationsPanel,
  inputCls,
} from "@/components/studio/ui";
import { ApiError, api } from "@/lib/api";
import type {
  ConnectionPreview,
  DoctolibLoginStatus,
  ImportJob,
  InstanceSummary,
  RunResult,
  StepDef,
  StepResult,
  TemplateDetail,
} from "@/types";

export default function ConfigurePage({
  params,
}: {
  params: Promise<{ template: string }>;
}) {
  const { template: templateName } = use(params);
  const name = decodeURIComponent(templateName);

  const [template, setTemplate] = useState<TemplateDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [violations, setViolations] = useState<string[]>([]);
  const [deploying, setDeploying] = useState(false);
  const [instance, setInstance] = useState<InstanceSummary | null>(null);
  // role -> bound connection id (a connected system of record). Unbound roles fall back to the
  // simulated connection on deploy.
  const [connections, setConnections] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // The latest version may be a draft (editing forks v+1); the cockpit only
        // configures published contracts, so walk down to the newest published one.
        const summaries = await api.listTemplates();
        const published = summaries
          .filter((t) => t.name === name && t.status === "published")
          .sort((a, b) => b.version - a.version)[0];
        if (!published) throw new Error(`no published version of '${name}'`);
        const detail = await api.getTemplate(name, published.version);
        if (cancelled) return;
        setTemplate(detail);
        const defaults: Record<string, unknown> = {};
        for (const [key, spec] of Object.entries(detail.contract.parameters ?? {})) {
          if (spec.default !== null && spec.default !== undefined) defaults[key] = spec.default;
        }
        setValues(defaults);
      } catch (e) {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  const deploy = useCallback(async () => {
    if (!template) return;
    setDeploying(true);
    setViolations([]);
    try {
      const deployed = await api.deployInstance({
        template: template.name,
        version: template.version,
        param_values: values,
        connections,
      });
      setInstance(deployed);
    } catch (e) {
      if (e instanceof ApiError && e.violations.length) setViolations(e.violations);
      else setViolations([e instanceof Error ? e.message : String(e)]);
    } finally {
      setDeploying(false);
    }
  }, [template, values, connections]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <StudioHeader active="studio" />
      <main className="mx-auto max-w-4xl px-5 py-8 sm:px-6">
        <Link
          href="/studio"
          className="font-mono text-xs text-muted-foreground hover:text-ink"
        >
          ← catalog
        </Link>

        {loadError && (
          <div className="mt-6">
            <Notice tone="error">{loadError}</Notice>
          </div>
        )}
        {!template && !loadError && (
          <p className="mt-6 text-sm text-muted-foreground">Loading template…</p>
        )}

        {template && (
          <>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <h1 className="font-display text-2xl tracking-tight text-ink">
                {template.name}
              </h1>
              <span className="rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                v{template.version}
              </span>
              <StatusChip status={template.status} />
              <RiskBadge risk={template.risk} />
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {template.agent_class}
              {template.agent_surface ? ` · ${template.agent_surface}` : ""} ·{" "}
              {template.step_count} steps
            </p>

            <EnforcedLoopStrip steps={template.contract.steps} />

            {/* The parameter form — auto-rendered from the template's schema (#9). */}
            <section className="mt-8 rounded-xl border border-border bg-surface p-5">
              <SectionTitle>Configure</SectionTitle>
              <div className="mt-4 space-y-4">
                {Object.keys(template.contract.parameters ?? {}).length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    This template declares no parameters.
                  </p>
                )}
                {Object.entries(template.contract.parameters ?? {}).map(([key, spec]) => (
                  <ParameterField
                    key={key}
                    name={key}
                    spec={spec}
                    value={values[key]}
                    onChange={(v) => setValues((prev) => ({ ...prev, [key]: v }))}
                  />
                ))}
              </div>
            </section>

            <section className="mt-6 rounded-xl border border-border bg-surface p-5">
              <SectionTitle>Connections</SectionTitle>
              <div className="mt-3 space-y-2">
                {template.contract.required_connections.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    This template requires no connections.
                  </p>
                )}
                {template.contract.required_connections.map((role) => (
                  <ConnectionRow
                    key={role}
                    role={role}
                    boundId={connections[role]}
                    onBound={(id) => setConnections((prev) => ({ ...prev, [role]: id }))}
                    onUnbind={() =>
                      setConnections((prev) => {
                        const next = { ...prev };
                        delete next[role];
                        return next;
                      })
                    }
                  />
                ))}
              </div>
            </section>

            {violations.length > 0 && (
              <div className="mt-6">
                <ViolationsPanel title="deploy refused" violations={violations} />
              </div>
            )}

            {!instance ? (
              <button
                type="button"
                onClick={deploy}
                disabled={deploying}
                className="mt-6 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {deploying ? "Deploying…" : "Deploy instance"}
              </button>
            ) : (
              <>
                <div className="mt-6">
                  <Notice tone="success">
                    Deployed instance{" "}
                    <span className="font-mono text-[13px]">{instance.instance_id}</span> —
                    pinned to {instance.template}@v{instance.template_version}.
                  </Notice>
                </div>
                {template.agent_class === "workflow" ? (
                  <ImportPanel instance={instance} />
                ) : (
                  <TestRunPanel template={template} instance={instance} />
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}

/** One required connection: connect a real thevea account (credentials encrypted server-side),
 *  or leave it to fall back to the simulated connection at deploy. */
function ConnectionRow({
  role,
  boundId,
  onBound,
  onUnbind,
}: {
  role: string;
  boundId?: string;
  onBound: (id: string) => void;
  onUnbind: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [agendaIds, setAgendaIds] = useState("");
  const [code, setCode] = useState("");
  const [login, setLogin] = useState<DoctolibLoginStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ConnectionPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // The destination is always thevea. The source can be the practice's existing admin system
  // (healthyfeet, username/password) or doctolib Pro. doctolib is a two-step server-side headless
  // login (username/password, then the emailed code on a new device); the others are one-shot
  // credential connections. All are encrypted server-side.
  const isSource = role === "source";
  const [sourceAdapter, setSourceAdapter] = useState("healthyfeet");
  const adapter = isSource ? sourceAdapter : "thevea";
  const isDoctolib = adapter === "doctolib";
  const label = !isSource ? "thevea" : isDoctolib ? "doctolib" : "source admin";
  const awaitingCode = login?.status === "awaiting_code";
  const ready = isDoctolib ? !!username && !!password && !!agendaIds : !!username && !!password;

  const reset = () => {
    setOpen(false);
    setPassword("");
    setCode("");
    setLogin(null);
  };

  // Poll a doctolib login job until it reaches one of `targets` (or times out).
  const pollLogin = async (jobId: string, targets: string[], maxMs = 150000) => {
    const start = Date.now();
    let st = await api.pollDoctolibLogin(jobId);
    while (!targets.includes(st.status) && Date.now() - start < maxMs) {
      await new Promise((r) => setTimeout(r, 2000));
      st = await api.pollDoctolibLogin(jobId);
    }
    return st;
  };

  const finish = (st: DoctolibLoginStatus) => {
    setLogin(st);
    if (st.status === "done" && st.connection_id) {
      onBound(st.connection_id);
      reset();
    } else if (st.status === "failed") {
      setError(st.error || "doctolib login failed");
    }
  };

  const connectDoctolib = async () => {
    setBusy(true);
    setError(null);
    try {
      const started = await api.startDoctolibLogin({
        username,
        password,
        agenda_ids: agendaIds.trim(),
      });
      finish(await pollLogin(started.job_id, ["awaiting_code", "done", "failed"]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async () => {
    if (!login) return;
    setBusy(true);
    setError(null);
    try {
      await api.submitDoctolibCode(login.job_id, code.trim());
      finish(await pollLogin(login.job_id, ["done", "failed"]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const connect = async () => {
    if (isDoctolib) return connectDoctolib();
    setBusy(true);
    setError(null);
    try {
      const conn = await api.createConnection({
        type: "calendar",
        adapter,
        credentials: { username, password },
      });
      onBound(conn.id);
      reset();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doPreview = async () => {
    if (!boundId) return;
    setPreviewing(true);
    setPreview(null);
    try {
      setPreview(await api.previewConnection(boundId));
    } catch (e) {
      setPreview({ ok: false, count: 0, raw: null, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-background/60 px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm text-ink">{role}</span>
        {boundId ? (
          <span className="flex items-center gap-2">
            <span className="rounded-md border border-success/20 bg-success/10 px-2 py-0.5 font-mono text-[11px] text-success">
              {label} — connected
            </span>
            {isSource && (
              <button
                type="button"
                onClick={doPreview}
                disabled={previewing}
                className="font-mono text-[11px] text-primary hover:underline disabled:opacity-50"
              >
                {previewing ? "reading…" : "preview →"}
              </button>
            )}
            <button
              type="button"
              onClick={onUnbind}
              className="font-mono text-[11px] text-muted-foreground hover:text-danger"
            >
              disconnect
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="font-mono text-[11px] text-primary hover:underline"
          >
            {open ? "cancel" : `connect ${label} →`}
          </button>
        )}
      </div>

      {!boundId && !open && (
        <p className="mt-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          not connected
        </p>
      )}

      {open && !boundId && (
        <div className="mt-3 space-y-2">
          {isSource && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono uppercase tracking-widest">source system</span>
              <select
                className={`${inputCls} w-auto`}
                value={sourceAdapter}
                onChange={(e) => setSourceAdapter(e.target.value)}
              >
                <option value="healthyfeet">healthyfeet (admin login)</option>
                <option value="doctolib">doctolib Pro (login)</option>
              </select>
            </label>
          )}
          {isDoctolib ? (
            <>
              <p className="text-xs text-muted-foreground">
                Enter the Doctolib Pro login. We sign in on the server (credentials encrypted at
                rest); on a new device Doctolib emails a 6-digit code you&apos;ll enter next.
                Read-only — never writes to Doctolib.
              </p>
              <input
                className={inputCls}
                placeholder="Doctolib Pro email"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
                disabled={awaitingCode}
              />
              <input
                className={inputCls}
                type="password"
                placeholder="Doctolib Pro password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                disabled={awaitingCode}
              />
              <input
                className={inputCls}
                placeholder="agenda ids, comma-separated (e.g. 2570190,2557171)"
                value={agendaIds}
                onChange={(e) => setAgendaIds(e.target.value)}
                autoComplete="off"
                disabled={awaitingCode}
              />
              {awaitingCode && (
                <>
                  <p className="font-mono text-[11px] uppercase tracking-widest text-primary">
                    enter the 6-digit code Doctolib emailed you
                  </p>
                  <input
                    className={inputCls}
                    placeholder="6-digit code"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    inputMode="numeric"
                    autoComplete="off"
                  />
                </>
              )}
            </>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Enter the {label} login. Credentials are encrypted at rest and used only to act on
                this calendar.
              </p>
              <input
                className={inputCls}
                placeholder={`${label} username or email`}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
              />
              <input
                className={inputCls}
                type="password"
                placeholder={`${label} password`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
              />
            </>
          )}
          {error && <Notice tone="error">{error}</Notice>}
          {awaitingCode ? (
            <button
              type="button"
              onClick={submitCode}
              disabled={busy || code.trim().length < 6}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Verifying…" : "Confirm code"}
            </button>
          ) : (
            <button
              type="button"
              onClick={connect}
              disabled={busy || !ready}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {busy ? (isDoctolib ? "Logging in…" : "Connecting…") : "Connect"}
            </button>
          )}
        </div>
      )}

      {preview && (
        <div className="mt-3 space-y-2">
          {preview.ok ? (
            <>
              <p className="font-mono text-[11px] uppercase tracking-widest text-success">
                read {preview.count} record(s) — the agent can access the calendar (read-only)
              </p>
              <pre className="max-h-64 overflow-auto rounded-md border border-border bg-background p-3 font-mono text-[11px] text-muted-foreground">
                {JSON.stringify(preview.raw, null, 2)}
              </pre>
            </>
          ) : (
            <Notice tone="error">{preview.error || "preview failed"}</Notice>
          )}
        </div>
      )}
    </div>
  );
}

/** Start a governed calendar import as a background job, then poll it for live progress. */
function ImportPanel({ instance }: { instance: InstanceSummary }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);

  const running = job?.status === "running";

  const start = async () => {
    setStarting(true);
    setError(null);
    setJob(null);
    try {
      setJob(await api.startImport(instance.instance_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  // While the job is running, poll its progress every 1.5s. The effect re-subscribes only when the
  // job id or status changes, so a progress update (still running) keeps the same interval going;
  // reaching completed/failed tears it down.
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const next = await api.getImportJob(instance.instance_id, job.job_id);
        if (!cancelled) setJob(next);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    }, 1500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [job?.job_id, job?.status, instance.instance_id]);

  const pct = job && job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;

  return (
    <section className="mt-6 rounded-xl border border-border bg-surface p-5">
      <SectionTitle>Import appointments</SectionTitle>
      <p className="mt-1 text-sm text-muted-foreground">
        Reads the source calendar and appends each appointment into thevea — every one verified
        against thevea&apos;s own read, idempotent, and <strong>append-only (never replaces)</strong>.
        Only <strong>confirmed, future</strong> appointments are imported; cancelled, rescheduled,
        still-new, and past bookings are excluded and listed below. Runs in the background — you can
        leave this page and come back.
      </p>
      <button
        type="button"
        onClick={start}
        disabled={starting || running}
        className="mt-3 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
      >
        {starting ? "Starting…" : running ? "Importing…" : "Run import"}
      </button>

      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {job && (
        <div className="mt-4 space-y-3">
          {running && (
            <div>
              <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                <span>Importing…</span>
                <span>
                  {job.done}
                  {job.total > 0 ? ` / ${job.total}` : ""}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all duration-500"
                  style={{ width: job.total > 0 ? `${pct}%` : "15%" }}
                />
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2 text-xs">
            <ReportPill label={`${job.total} eligible`} />
            <ReportPill label={`${job.created.length} created`} dot="bg-success" />
            <ReportPill label={`${job.skipped.length} skipped`} dot="bg-warning" />
            <ReportPill label={`${job.failed.length} failed`} dot="bg-danger" />
            {job.excluded.length > 0 && (
              <ReportPill label={`${job.excluded.length} excluded`} dot="bg-muted-foreground" />
            )}
          </div>
          {job.status === "failed" && (
            <Notice tone="error">import failed{job.error ? `: ${job.error}` : ""}</Notice>
          )}
          {job.failed.length > 0 && (
            <ul className="space-y-1">
              {job.failed.map((f) => (
                <li key={f.ref} className="font-mono text-[13px] text-danger">
                  {f.ref}: {f.status}
                  {f.reason ? ` — ${f.reason}` : ""}
                </li>
              ))}
            </ul>
          )}
          {job.excluded.length > 0 && (
            <details className="text-[13px]">
              <summary className="cursor-pointer text-muted-foreground">
                {job.excluded.length} excluded (not confirmed / in the past)
              </summary>
              <ul className="mt-1 space-y-1">
                {job.excluded.map((x) => (
                  <li key={x.ref} className="font-mono text-muted-foreground">
                    {x.ref} — {x.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function ReportPill({ label, dot }: { label: string; dot?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/60 px-2.5 py-1 text-muted-foreground">
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />}
      {label}
    </span>
  );
}

/** The contract at a glance: each enforced step as precondition -> tool -> postcondition. */
function EnforcedLoopStrip({ steps }: { steps: StepDef[] }) {
  const enforced = steps.filter((s) => s.kind === "enforced");
  if (enforced.length === 0) return null;
  return (
    <section className="mt-6 rounded-xl border border-border bg-surface p-5">
      <SectionTitle>The enforced loop</SectionTitle>
      <div className="mt-3 space-y-3">
        {enforced.map((step) => (
          <div key={step.id} className="rounded-md border border-border bg-background/60 p-3">
            <div className="font-mono text-sm text-ink">{step.id}</div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
              {(step.preconditions ?? []).map((c, i) => (
                <span
                  key={`pre-${i}`}
                  className="rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-muted-foreground"
                >
                  pre: {c.expr}
                </span>
              ))}
              {(step.tools ?? []).map((t) => (
                <span
                  key={t}
                  className="rounded-md border border-primary/30 bg-primary/10 px-1.5 py-0.5 font-mono text-primary"
                >
                  tool: {t}
                </span>
              ))}
              {(step.postconditions ?? []).map((c, i) => (
                <span
                  key={`post-${i}`}
                  className="rounded-md border border-border bg-muted px-1.5 py-0.5 font-mono text-muted-foreground"
                >
                  post: {c.expr}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Manually trigger a governed run of the deployed instance and render the verdicts. */
function TestRunPanel({
  template,
  instance,
}: {
  template: TemplateDetail;
  instance: InstanceSummary;
}) {
  const initialCase = useMemo(() => {
    // A plausible case fixture: one empty object per declared state binding.
    const fixture: Record<string, unknown> = {};
    for (const binding of Object.keys(template.contract.state ?? {})) fixture[binding] = {};
    return JSON.stringify(fixture, null, 2);
  }, [template]);

  const [caseText, setCaseText] = useState(initialCase);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const parsed: unknown = JSON.parse(caseText);
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("the case must be a JSON object keyed by state binding");
      }
      setResult(await api.runInstance(instance.instance_id, parsed as Record<string, unknown>));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="mt-6 rounded-xl border border-border bg-surface p-5">
      <SectionTitle>Test run</SectionTitle>
      <p className="mt-1 text-sm text-muted-foreground">
        Trigger a governed run against a case fixture — the engine rules on every enforced
        step and the run lands in{" "}
        <Link href="/runs" className="underline hover:text-ink">
          Runs
        </Link>
        .
      </p>
      <Field label="case (state per binding)" className="mt-4">
        <textarea
          value={caseText}
          onChange={(e) => setCaseText(e.target.value)}
          rows={8}
          spellCheck={false}
          className={`${inputCls} font-mono text-[13px]`}
        />
      </Field>
      <button
        type="button"
        onClick={run}
        disabled={running}
        className="mt-3 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
      >
        {running ? "Running…" : "Run"}
      </button>

      {error && (
        <div className="mt-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      {result && (
        <div className="mt-4 space-y-2">
          <div className="font-mono text-xs text-muted-foreground">
            run {result.run_id.slice(0, 8)}
          </div>
          {result.steps.map((step) => (
            <StepVerdict key={step.step_id} step={step} />
          ))}
        </div>
      )}
    </section>
  );
}

const VERDICT_STYLE: Record<string, { chip: string; label: string }> = {
  ok: { chip: "border-success/20 bg-success/10 text-success", label: "OK" },
  blocked: { chip: "border-danger/20 bg-danger/10 text-danger", label: "BLOCKED" },
  rejected: { chip: "border-warning/20 bg-warning/10 text-warning", label: "REJECTED" },
  state_unavailable: {
    chip: "border-warning/20 bg-warning/10 text-warning",
    label: "STATE UNAVAILABLE",
  },
};

function StepVerdict({ step }: { step: StepResult }) {
  const style = VERDICT_STYLE[step.status] ?? VERDICT_STYLE.ok;
  return (
    <div className="rounded-md border border-border bg-background/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${style.chip}`}>
          {style.label}
        </span>
        <span className="font-mono text-sm text-ink">{step.step_id}</span>
      </div>
      {step.reason && <p className="mt-2 text-sm text-foreground">{step.reason}</p>}
      {step.expr && (
        <p className="mt-1 font-mono text-[13px] text-muted-foreground">{step.expr}</p>
      )}
      {step.blocked_tool && (
        <p className="mt-1 font-mono text-[13px] text-danger">
          blocked tool: {step.blocked_tool}
        </p>
      )}
    </div>
  );
}