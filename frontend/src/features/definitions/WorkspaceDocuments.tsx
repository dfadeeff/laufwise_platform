"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DefinitionContent, KnowledgeDocument } from "@/types";

// The workspace's own documents a workflow works from (ADR-0041 step 2): a lease, a decision of
// the owners. A person chooses them here; they are added and edited on the workspace's Documents
// page (ADR-0017). The draft carries their text as it stands, refreshed at every save, builder
// run and publish; a published version keeps the copy it was published with.

export function WorkspaceDocuments({
  definitionKey,
  content,
  canChoose,
  onChanged,
}: {
  definitionKey: string;
  content: DefinitionContent;
  /** A draft of the workspace's own workflow, with no unsaved edits and no builder run. */
  canChoose: boolean;
  onChanged: () => void;
}) {
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chosen = (content.knowledge ?? []).map((k) => k.id);

  useEffect(() => {
    if (!canChoose) return;
    api.listKnowledge().then((r) => setDocuments(r.documents)).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [canChoose]);

  const toggle = async (id: string, on: boolean) => {
    setBusy(true);
    setError("");
    try {
      await api.setDefinitionKnowledge(definitionKey, on ? [...chosen, id] : chosen.filter((x) => x !== id));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  return (
    <section className="studio-section mt-6 p-5 sm:p-6">
      <h2 className="text-lg font-semibold text-ink">Your documents it works from</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        Leases, decisions of the owners, anything of this workspace the builder should read on every run.
        They are facts for it, not instructions. A published version keeps the text it was published with.{" "}
        <Link href="/studio/governance/knowledge" className="underline">Add or edit documents</Link>
      </p>
      {canChoose ? (
        documents === null ? (
          <p className="mt-4 text-sm text-muted-foreground">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">This workspace has no documents yet.</p>
        ) : (
          <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-white">
            {documents.map((doc) => (
              <li key={doc.id} className="px-4 py-2.5">
                <label className="flex items-start gap-3 text-sm text-ink">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={chosen.includes(doc.id)}
                    disabled={busy}
                    onChange={(e) => void toggle(doc.id, e.target.checked)}
                  />
                  <span className="min-w-0">
                    <span className="block break-words font-medium">{doc.title}</span>
                    <span className="text-[12px] text-muted-foreground">{doc.chars.toLocaleString()} characters</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )
      ) : (content.knowledge ?? []).length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">None.</p>
      ) : (
        <ul className="mt-4 space-y-2">
          {(content.knowledge ?? []).map((k) => (
            <li key={k.id}>
              <details className="rounded-lg border border-border bg-white p-3">
                <summary className="cursor-pointer text-[13px] font-medium text-ink">{k.title}</summary>
                <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-5 text-ink">{k.content}</pre>
              </details>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" className="mt-3 break-words text-sm text-danger">{error}</p>}
    </section>
  );
}
