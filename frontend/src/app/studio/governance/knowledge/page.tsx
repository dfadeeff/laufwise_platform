"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { KnowledgeDocument } from "@/types";
import { Field, Section } from "@/features/agents/Fields";

// The workspace's documents for its agents (ADR-0017): FAQ, insurance rules, "what to bring".
// Each agent chooses which it knows in its Knowledge base section, and publishing pins their
// content — so an edit here reaches a caller only after the agent is published again.

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.readAsDataURL(file);
  });
}

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [limit, setLimit] = useState(40000);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null); // a document id, or "new"
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = () =>
    api
      .listKnowledge()
      .then((r) => {
        setDocuments(r.documents);
        setLimit(r.max_agent_chars);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  useEffect(() => {
    void load();
  }, []);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setEditing(null);
      setTitle("");
      setContent("");
      setNotice(done);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setError("");
    try {
      const doc = await api.getKnowledge(id);
      setTitle(doc.title);
      setContent(doc.content ?? "");
      setEditing(id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const [address, setAddress] = useState("");

  async function importPage() {
    await run(
      () => api.addKnowledgeUrl(address.trim(), title.trim()),
      "Page imported. Review the text, then choose it in an agent's Knowledge base.",
    );
    setAddress("");
  }

  async function upload(file: File) {
    const name = title.trim() || file.name.replace(/\.pdf$/i, "");
    await run(
      async () => api.addKnowledgePdf(name, await toBase64(file)),
      "PDF added. Choose it in an agent's Knowledge base, then publish the agent.",
    );
  }

  return (
    <main className="mx-auto min-h-screen max-w-5xl px-4 py-9 sm:px-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Documents</h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Every document in this workspace, shared by all your agents. You usually add documents
            from an agent's Knowledge base, where they are ticked for that agent; here you can
            review, edit or delete them. Changes reach callers when you publish the agent again.
          </p>
        </div>
        <button
          className="studio-primary"
          onClick={() => {
            setEditing(editing === "new" ? null : "new");
            setTitle("");
            setContent("");
          }}
        >
          {editing === "new" ? "Close" : "+ Add document"}
        </button>
      </div>
      {error && (
        <p className="mt-5 rounded-lg bg-danger/10 p-4 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-5 rounded-lg bg-success/10 p-4 text-sm text-success" role="status">
          {notice}
        </p>
      )}

      {editing && (
        <form
          className="mt-7"
          onSubmit={(e) => {
            e.preventDefault();
            void run(
              () =>
                editing === "new"
                  ? api.addKnowledgeText(title.trim(), content)
                  : api.updateKnowledge(editing, title.trim(), content),
              editing === "new"
                ? "Document added. Choose it in an agent's Knowledge base, then publish the agent."
                : "Saved. Agents that know this document say it after you publish them again.",
            );
          }}
        >
          <Section
            title={editing === "new" ? "New document" : "Edit document"}
            description="Write it the way you would explain it to a new receptionist. Plain text; no formatting needed."
          >
            <Field label="Title">
              <input
                required
                maxLength={200}
                className="studio-input"
                value={title}
                placeholder="Versicherung und Rezepte"
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <Field
              label="Text"
              hint={`${content.length.toLocaleString()} of at most ${limit.toLocaleString()} characters an agent can know in total.`}
            >
              <textarea
                required
                rows={14}
                className="studio-input font-normal"
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" disabled={busy} className="studio-primary">
                {busy ? "Saving…" : "Save document"}
              </button>
              {editing === "new" && (
                <span className="flex min-w-0 flex-1 basis-72 flex-wrap gap-2">
                  <input
                    className="studio-input min-w-0 flex-1"
                    inputMode="url"
                    placeholder="…or import a web page: https://www.ihre-praxis.de/faq"
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                  />
                  <button
                    type="button"
                    className="studio-secondary"
                    disabled={busy || !address.trim()}
                    onClick={() => void importPage()}
                  >
                    Import page
                  </button>
                </span>
              )}
              {editing === "new" && (
                <label className="studio-secondary cursor-pointer">
                  Upload a PDF instead
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    className="sr-only"
                    disabled={busy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void upload(file);
                      e.target.value = "";
                    }}
                  />
                </label>
              )}
            </div>
          </Section>
        </form>
      )}

      <div className="mt-8 space-y-4">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading documents…</p>
        ) : documents.length === 0 ? (
          <Section
            title="No documents yet"
            description="Add your FAQ or paste the text of your website's practice page. Your agents answer only from what you give them, and take a callback for anything else."
          >
            <span />
          </Section>
        ) : (
          documents.map((d) => (
            <Section
              key={d.id}
              title={d.title}
              description={`${d.source === "pdf" ? "From a PDF" : d.source === "url" ? "From a web page" : "Text"} · ${d.chars.toLocaleString()} characters · updated ${new Date(d.updated_at).toLocaleDateString()}`}
            >
              <div className="flex flex-wrap gap-4">
                <button className="text-sm text-primary" onClick={() => void open(d.id)}>
                  Edit →
                </button>
                <button
                  className="text-sm text-danger disabled:opacity-40"
                  disabled={busy}
                  onClick={() =>
                    void run(() => api.deleteKnowledge(d.id), "Document deleted.")
                  }
                >
                  Delete
                </button>
              </div>
            </Section>
          ))
        )}
      </div>
    </main>
  );
}
