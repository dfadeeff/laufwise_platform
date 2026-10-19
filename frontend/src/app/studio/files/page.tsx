"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/studio/icons";
import { StudioTrail } from "@/components/studio/WorkspaceShell";
import type { StoredFile } from "@/types";

// The workspace's files: the documents its workflows read (ADR-0029). Kept as they came, checked
// by their content rather than their name, and visible to this workspace only. Nothing here reads
// a file yet; this is where the documents a workflow will process are put.

const KINDS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "text/plain": "Text",
  "text/csv": "CSV",
};

const size = (bytes: number) =>
  bytes < 1024
    ? `${bytes} B`
    : bytes < 1024 * 1024
      ? `${Math.round(bytes / 1024)} KB`
      : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

type Outcome = { name: string; ok: boolean; message: string };

export default function FilesPage() {
  const [files, setFiles] = useState<StoredFile[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = () => {
    setError("");
    api
      .listFiles()
      .then(setFiles)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, []);

  // One at a time, so each file gets its own answer: stored, already here, or why not.
  const upload = async (chosen: File[]) => {
    if (chosen.length === 0) return;
    setBusy(true);
    const results: Outcome[] = [];
    const known = new Set((files ?? []).map((f) => f.id));
    for (const file of chosen) {
      try {
        const stored = await api.uploadFile(file);
        results.push({
          name: file.name,
          ok: true,
          message: known.has(stored.id)
            ? `already stored as “${stored.name}”`
            : `stored as ${KINDS[stored.media_type] ?? stored.media_type}`,
        });
        known.add(stored.id);
      } catch (e) {
        results.push({
          name: file.name,
          ok: false,
          message: e instanceof Error ? e.message : String(e),
        });
      }
    }
    setOutcomes(results);
    setBusy(false);
    load();
  };

  const download = async (file: StoredFile) => {
    try {
      const url = URL.createObjectURL(await api.downloadFile(file.id));
      const link = document.createElement("a");
      link.href = url;
      link.download = file.name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (file: StoredFile) => {
    if (!window.confirm(`Delete “${file.name}”? This cannot be undone.`)) return;
    try {
      await api.deleteFile(file.id);
      setFiles((files ?? []).filter((f) => f.id !== file.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <main className="mx-auto max-w-[1100px] px-5 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "Files" }]} />
      <p className="studio-eyebrow text-primary">Documents your workflows read</p>
      <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">Files</h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        PDF, PNG, JPEG or text, up to 15 MB each. A file is checked by its
        content, not its name, and only this workspace can see it.
      </p>

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!busy) upload(Array.from(e.dataTransfer.files));
        }}
        className={`mt-6 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-10 text-center transition ${dragging ? "border-primary bg-accent" : "border-border bg-white hover:border-primary/40"}`}
      >
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-muted text-muted-foreground">
          <Icon name="file" size={18} />
        </span>
        <span className="text-sm font-medium text-ink">
          {busy ? "Uploading…" : "Drop files here, or choose them"}
        </span>
        <span className="text-xs text-muted-foreground">
          The same file uploaded twice is kept once.
        </span>
        <input
          ref={input}
          type="file"
          multiple
          disabled={busy}
          accept=".pdf,.png,.jpg,.jpeg,.txt,.csv,application/pdf,image/png,image/jpeg,text/plain,text/csv"
          className="sr-only"
          onChange={(e) => {
            upload(Array.from(e.target.files ?? []));
            if (input.current) input.current.value = "";
          }}
        />
      </label>

      {outcomes.length > 0 && (
        <ul className="mt-4 space-y-1.5 text-sm" aria-live="polite">
          {outcomes.map((o, i) => (
            <li key={`${o.name}-${i}`} className={o.ok ? "text-ink" : "text-danger"}>
              <span className="font-medium">{o.name}</span>: {o.message}
            </li>
          ))}
        </ul>
      )}

      {error && (
        <div
          role="alert"
          className="mt-6 rounded-lg border border-danger/20 bg-danger/5 p-4 text-sm text-danger"
        >
          {error}
          <button className="ml-3 underline" onClick={load}>
            Retry
          </button>
        </div>
      )}

      {!files && !error && (
        <p role="status" className="mt-10 text-sm text-muted-foreground">
          Loading files…
        </p>
      )}

      {files && (
        <section className="mt-10">
          <h2 className="text-[15px] font-semibold text-ink">
            All files
            <span className="ml-2 font-normal text-muted-foreground">{files.length}</span>
          </h2>
          {files.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">No files yet.</p>
          ) : (
            <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-white">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {["Name", "Type", "Size", "Uploaded", ""].map((h, i) => (
                      <th
                        key={`${h}-${i}`}
                        scope="col"
                        className={`studio-eyebrow px-4 py-2.5 font-semibold ${i === 2 ? "text-right" : ""}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {files.map((f) => (
                    <tr key={f.id} className="border-b border-border last:border-0">
                      <td className="max-w-[320px] truncate px-4 py-3 font-medium text-ink" title={f.name}>
                        {f.name}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {KINDS[f.media_type] ?? f.media_type}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {size(f.size_bytes)}
                      </td>
                      <td className="px-4 py-3 text-[13px] text-muted-foreground">
                        {f.created_at ? new Date(f.created_at).toLocaleString() : "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right">
                        <button className="studio-secondary" onClick={() => download(f)}>
                          Download
                        </button>
                        <button
                          className="studio-secondary ml-2 text-danger"
                          onClick={() => remove(f)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
