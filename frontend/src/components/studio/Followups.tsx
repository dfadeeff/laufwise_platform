"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
export function Followups() {
  const [rows, setRows] = useState<
    Awaited<ReturnType<typeof api.listFollowups>>
  >([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const load = () =>
    api
      .listFollowups()
      .then((tasks) =>
        setRows(
          tasks.filter(
            (t) => t.context.conversation_id && t.status !== "completed",
          ),
        ),
      )
      .catch((e) => setError(e.message));
  useEffect(() => {
    void load();
  }, []);
  async function update(id: string, action: "claim" | "complete") {
    setBusy(id);
    setError("");
    try {
      await api.updateFollowup(id, action);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }
  if (!rows.length && !error) return null;
  return (
    <section className="studio-section mb-7">
      <h2 className="font-semibold">Needs your attention</h2>
      {error && (
        <p className="mt-3 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      {rows.map((t) => (
        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"
          key={t.task_id}
        >
          <div>
            <Link
              className="text-sm font-medium text-primary"
              href={`/studio/history?call=${t.context.conversation_id}`}
              onClick={() => {
                window.location.href = `/studio/history?call=${t.context.conversation_id}`;
              }}
            >
              {t.context.reason === "notification_failed"
                ? "Call summary needs delivery"
                : "Staff callback requested"}{" "}
              →
            </Link>
            <p className="mt-1 text-xs text-muted-foreground">
              {t.context.assigned_to
                ? "Assigned to a team member"
                : "Unassigned"}
            </p>
          </div>
          <div className="flex gap-2">
            {!t.context.assigned_to && (
              <button
                disabled={busy === t.task_id}
                className="studio-secondary"
                onClick={() => update(t.task_id, "claim")}
              >
                Take callback
              </button>
            )}
            <button
              disabled={busy === t.task_id}
              className="studio-secondary"
              onClick={() => update(t.task_id, "complete")}
            >
              Mark resolved
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
