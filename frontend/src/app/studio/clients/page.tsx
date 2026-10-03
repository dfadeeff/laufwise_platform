"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth, useOrganizationList } from "@clerk/nextjs";
import { api } from "@/lib/api";
import type { WorkspaceSummary } from "@/types";
import { StudioTrail } from "@/components/studio/WorkspaceShell";

// Every workspace this login belongs to, side by side (ADR-0016). An agency runs several
// practices; a practice owner usually has one. Each card is read with a token Clerk issues for
// that organization, so the page sees exactly the workspaces the login is a member of, and
// reading them never changes which one is active.

type Loaded = WorkspaceSummary | { error: string };

export default function WorkspacesPage() {
  const router = useRouter();
  const { getToken } = useAuth();
  const { isLoaded, userMemberships, setActive, createOrganization } = useOrganizationList({
    userMemberships: { infinite: true },
  });
  const [summaries, setSummaries] = useState<Record<string, Loaded>>({});
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Pull every page of memberships: an agency with many clients must see all of them.
  useEffect(() => {
    if (userMemberships?.hasNextPage && !userMemberships.isFetching) userMemberships.fetchNext?.();
  }, [userMemberships]);

  const memberships = userMemberships?.data ?? [];
  const ids = memberships.map((m) => m.organization.id).join(",");

  useEffect(() => {
    for (const membership of memberships) {
      const id = membership.organization.id;
      if (summaries[id]) continue;
      getToken({ organizationId: id })
        .then((token) => {
          if (!token) throw new Error("No session for this workspace.");
          return api.workspaceSummary(token);
        })
        .then((summary) => setSummaries((all) => ({ ...all, [id]: summary })))
        .catch((e) =>
          setSummaries((all) => ({
            ...all,
            [id]: { error: e instanceof Error ? e.message : String(e) },
          })),
        );
    }
    // `ids` changes exactly when a membership is added or removed.
  }, [ids]);

  async function open(id: string, path = "/studio") {
    if (!setActive) return;
    await setActive({ organization: id });
    router.push(path);
  }

  async function createClient() {
    if (!createOrganization || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const organization = await createOrganization({ name: name.trim() });
      setName("");
      // Straight into the new workspace, where the empty agent list is the setup path.
      await open(organization.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-[1100px] px-4 py-8 sm:px-8 sm:py-10">
      <StudioTrail crumbs={[{ label: "All workspaces" }]} />
      <p className="studio-eyebrow text-primary">Every practice you run</p>
      <h1 className="mt-2.5 text-[28px] font-semibold tracking-tight text-ink">
        All workspaces
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        Each practice is its own workspace with its own agents, calendar, numbers and calls.
        See what each one needs here, and open it to work on it.
      </p>

      <form
        className="studio-section mt-6 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void createClient();
        }}
      >
        <label className="block min-w-0 flex-1 basis-60">
          <span className="studio-label">New client practice</span>
          <input
            className="studio-input mt-1.5"
            value={name}
            maxLength={120}
            placeholder="Praxis Dr. Weber"
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !name.trim()} className="studio-primary">
          {busy ? "Creating…" : "Create workspace"}
        </button>
      </form>
      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-danger/10 p-4 text-sm text-danger">
          {error}
        </p>
      )}

      {!isLoaded ? (
        <p className="mt-8 text-sm text-muted-foreground">Loading workspaces…</p>
      ) : memberships.length === 0 ? (
        <p className="mt-8 text-sm text-muted-foreground">
          This login belongs to no workspace yet. Create one above.
        </p>
      ) : (
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          {memberships.map((membership) => {
            const id = membership.organization.id;
            const summary = summaries[id];
            const ready = summary && !("error" in summary);
            const live = ready ? summary.agents.filter((a) => a.live).length : 0;
            return (
              <section key={id} className="studio-section flex flex-col gap-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-[15px] font-semibold text-ink">
                      {membership.organization.name}
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {ready
                        ? `${summary.agents.length} agent${summary.agents.length === 1 ? "" : "s"} · ${live} answering calls · ${summary.calls_7d} calls this week`
                        : summary
                          ? "Could not load this workspace."
                          : "Loading…"}
                    </p>
                  </div>
                  <button className="studio-secondary" onClick={() => void open(id)}>
                    Open
                  </button>
                </div>
                {ready && (
                  <>
                    <div className="flex flex-wrap gap-2 text-xs">
                      <span className="chip">
                        {summary.calendars ? "Calendar connected" : "No calendar"}
                      </span>
                      <span className="chip">
                        {summary.numbers.length
                          ? `${summary.numbers.length} number${summary.numbers.length === 1 ? "" : "s"}`
                          : "No number"}
                      </span>
                      {summary.callbacks_waiting > 0 && (
                        <span className="chip">{summary.callbacks_waiting} callbacks waiting</span>
                      )}
                    </div>
                    {summary.attention.length ? (
                      <ul className="space-y-1 text-sm text-warning">
                        {summary.attention.map((item) => (
                          <li key={item}>• {item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-success">Live and nothing waiting.</p>
                    )}
                  </>
                )}
                {summary && "error" in summary && (
                  <p className="text-sm text-danger">{summary.error}</p>
                )}
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
