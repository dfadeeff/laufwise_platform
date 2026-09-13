"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { Notice, RiskBadge, SectionTitle, StatusChip } from "@/components/studio/ui";
import type { InstanceSummary, TemplateSummary } from "@/types";

// The Studio catalog — configuration-tier entry point. Published templates as cards
// (deploy path), deployed instances below, and the authoring tier a click away.

interface CatalogEntry {
  name: string;
  published?: TemplateSummary; // latest published version
  draft?: TemplateSummary; // latest draft version
}

function groupCatalog(templates: TemplateSummary[]): CatalogEntry[] {
  const byName = new Map<string, CatalogEntry>();
  for (const t of templates) {
    const entry = byName.get(t.name) ?? { name: t.name };
    if (t.status === "published") {
      if (!entry.published || t.version > entry.published.version) entry.published = t;
    } else if (!entry.draft || t.version > entry.draft.version) {
      entry.draft = t;
    }
    byName.set(t.name, entry);
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function TemplateCard({ entry }: { entry: CatalogEntry }) {
  const t = entry.published;
  if (!t) {
    // Draft-only template: not deployable yet — route to the authoring tier.
    const d = entry.draft;
    return (
      <div className="flex flex-col rounded-xl border border-dashed border-border bg-surface p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-ink">{entry.name}</span>
          <StatusChip status="draft" />
          {d && <span className="font-mono text-[11px] text-muted-foreground">v{d.version}</span>}
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          Not published yet — finish it in the authoring tier to make it deployable.
        </p>
        <Link
          href={`/studio/author?template=${encodeURIComponent(entry.name)}`}
          className="mt-4 font-mono text-xs text-primary hover:underline"
        >
          continue in author →
        </Link>
      </div>
    );
  }
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface p-5 transition-colors hover:border-primary/40">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-ink">{t.name}</span>
        <span className="rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          v{t.version}
        </span>
        {entry.draft && entry.draft.version > t.version && <StatusChip status="draft" />}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <RiskBadge risk={t.risk} />
        <span className="rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          {t.agent_class}
        </span>
        <span className="rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          {t.step_count} steps
        </span>
      </div>
      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <Link
          href={`/studio/configure/${encodeURIComponent(t.name)}`}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Configure & deploy
        </Link>
        <Link
          href={`/studio/author?template=${encodeURIComponent(t.name)}`}
          className="font-mono text-xs text-muted-foreground hover:text-primary"
        >
          edit →
        </Link>
      </div>
    </div>
  );
}

function InstanceRow({
  instance,
  pausing,
  onPause,
}: {
  instance: InstanceSummary;
  pausing: boolean;
  onPause: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3 last:border-b-0">
      <span className="w-28 shrink-0 truncate font-mono text-xs text-muted-foreground">
        {instance.instance_id.slice(0, 12)}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm text-ink">
        {instance.template}
        <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
          v{instance.template_version}
        </span>
      </span>
      {instance.phone_number && (
        <span className="hidden font-mono text-[11px] text-muted-foreground sm:inline">
          {instance.phone_number}
        </span>
      )}
      <StatusChip status={instance.status} />
      {instance.status === "deployed" && (
        <button
          type="button"
          onClick={onPause}
          disabled={pausing}
          className="rounded-md border border-border px-2.5 py-1 font-mono text-xs text-muted-foreground hover:text-warning disabled:opacity-50"
        >
          {pausing ? "pausing…" : "pause"}
        </button>
      )}
    </div>
  );
}

export default function StudioPage() {
  const [templates, setTemplates] = useState<TemplateSummary[] | null>(null);
  const [instances, setInstances] = useState<InstanceSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pausingId, setPausingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [t, i] = await Promise.all([api.listTemplates(), api.listInstances()]);
      setTemplates(t);
      setInstances(i);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pause = async (id: string) => {
    setPausingId(id);
    setActionError(null);
    try {
      const updated = await api.pauseInstance(id);
      setInstances((prev) =>
        prev ? prev.map((i) => (i.instance_id === id ? updated : i)) : prev,
      );
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPausingId(null);
    }
  };

  const loading = templates === null && loadError === null;
  const catalog = templates ? groupCatalog(templates) : [];

  return (
    <div className="min-h-screen bg-background">
      <StudioHeader active="studio" />
      <main className="mx-auto max-w-7xl px-5 py-8 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-ink">Studio</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Pick a published use case, fill its form, deploy a governed instance.
            </p>
          </div>
          <Link
            href="/studio/voice"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Test conversational agent
          </Link>
        </div>

        {loadError && (
          <div className="mt-6">
            <Notice tone="error">
              <div className="font-medium">Control plane unreachable</div>
              <div className="mt-1 font-mono text-xs text-muted-foreground">
                {loadError} — API at {api._baseUrl}
              </div>
              <button
                type="button"
                onClick={() => void load()}
                className="mt-2 rounded-md border border-border bg-surface px-3 py-1 font-mono text-xs text-foreground hover:bg-muted"
              >
                retry
              </button>
            </Notice>
          </div>
        )}

        {loading && (
          <p className="mt-6 font-mono text-xs text-muted-foreground">loading catalog…</p>
        )}

        {templates && (
          <section className="mt-8">
            <SectionTitle>Catalog</SectionTitle>
            {catalog.length === 0 ? (
              <div className="mt-3 rounded-xl border border-dashed border-border bg-surface p-8 text-center">
                <p className="text-sm text-muted-foreground">
                  No templates yet. The catalog fills up as templates are authored and published.
                </p>
                <Link
                  href="/studio/author"
                  className="mt-3 inline-block font-mono text-xs text-primary hover:underline"
                >
                  open the authoring tier →
                </Link>
              </div>
            ) : (
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {catalog.map((entry) => (
                  <TemplateCard key={entry.name} entry={entry} />
                ))}
              </div>
            )}
          </section>
        )}

        {instances && (
          <section className="mt-10">
            <SectionTitle>Deployed instances</SectionTitle>
            {actionError && (
              <div className="mt-3">
                <Notice tone="error">{actionError}</Notice>
              </div>
            )}
            {instances.length === 0 ? (
              <p className="mt-3 text-sm text-muted-foreground">
                Nothing deployed yet — configure a template above to create the first instance.
              </p>
            ) : (
              <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
                {instances.map((instance) => (
                  <InstanceRow
                    key={instance.instance_id}
                    instance={instance}
                    pausing={pausingId === instance.instance_id}
                    onPause={() => void pause(instance.instance_id)}
                  />
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
