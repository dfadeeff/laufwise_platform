"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import type { DefinitionList, DefinitionSummary } from "@/types";

// The workflows this workspace builds (ADR-0039 step 5). A shipped template is the platform's:
// every workspace may use it and copy it, none may change it. A copy is the workspace's own,
// private draft: edited, tested and published here, through the same gate as the templates.

function Versions({ d }: { d: DefinitionSummary }) {
  return (
    <span className="flex flex-wrap gap-1.5 text-[11px]">
      {d.published_version !== null && (
        <span className="rounded-full border border-success/25 bg-success/10 px-2 py-0.5 font-medium text-success">
          Published v{d.published_version}
        </span>
      )}
      {d.draft_version !== null && (
        <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-medium text-muted-foreground">
          Draft v{d.draft_version}
        </span>
      )}
    </span>
  );
}

function CopyTemplate({ template, onCopied }: { template: DefinitionSummary; onCopied: (key: string) => void }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState(template.label);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!open)
    return (
      <button className="studio-secondary" onClick={() => setOpen(true)}>
        Copy into this workspace
      </button>
    );
  return (
    <form
      className="mt-3 flex w-full flex-col gap-2 sm:flex-row sm:items-center"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          onCopied((await api.copyTemplate(template.key, label.trim())).key);
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
          setBusy(false);
        }
      }}
    >
      <input
        aria-label="Name of your copy"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        className="studio-input h-9 w-full sm:max-w-xs"
      />
      <button className="studio-primary" disabled={busy || !label.trim()}>
        {busy ? "Copying…" : "Create draft"}
      </button>
      <button type="button" className="studio-secondary" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <p className="text-sm text-danger">{error}</p>}
    </form>
  );
}

export default function DefinitionsPage() {
  const router = useRouter();
  const [list, setList] = useState<DefinitionList | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listDefinitions().then(setList).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Agents", href: "/studio" }, { label: "Workflows" }]} />
      <p className="studio-eyebrow text-primary">Build</p>
      <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">Workflows</h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        A workflow is what a workflow agent runs: its steps, its rules, its calculations and the
        test cases it must pass. Start from a template; your copy is private to this workspace and
        is published only when every test case passes.
      </p>

      {error && (
        <p role="alert" className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger">
          {error}
        </p>
      )}
      {!list && !error && <p role="status" className="mt-6 text-sm text-muted-foreground">Loading…</p>}

      {list && (
        <>
          <section className="mt-8">
            <h2 className="text-lg font-semibold text-ink">Your workflows</h2>
            {list.own.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">None yet. Copy a template below to start one.</p>
            ) : (
              <div className="mt-4 grid gap-4 lg:grid-cols-2">
                {list.own.map((d) => (
                  <Link
                    key={d.key}
                    href={`/studio/definitions/${d.key}`}
                    className="studio-section group p-5 no-underline transition hover:border-primary/40"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-[15px] font-semibold text-ink group-hover:text-primary">{d.label}</h3>
                        <p className="mt-0.5 text-[13px] text-muted-foreground">From the template {d.template}</p>
                      </div>
                      <Versions d={d} />
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>

          <section className="mt-10">
            <h2 className="text-lg font-semibold text-ink">Templates</h2>
            <p className="mt-1 text-sm text-muted-foreground">Shipped with the platform. Read them here; copy one to change it.</p>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              {list.templates.map((t) => (
                <div key={t.key} className="studio-section p-5">
                  <div className="flex items-start gap-3">
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground">
                      <Icon name="file" size={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-[15px] font-semibold text-ink">{t.label}</h3>
                        <Versions d={t} />
                      </div>
                      <p className="mt-1 text-[13px] text-muted-foreground">{t.summary}</p>
                    </div>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3.5">
                    <Link href={`/studio/definitions/${t.key}`} className="studio-secondary no-underline">
                      Read it
                    </Link>
                    <CopyTemplate template={t} onCopied={(key) => router.push(`/studio/definitions/${key}`)} />
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}
