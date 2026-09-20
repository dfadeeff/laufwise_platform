import type { ConversationSummary } from "@/types";
import type { AgentConfig, StudioAgent } from "./types";

// What the workspace can honestly say about an agent, derived from records that already exist:
// saved calls, and the difference between the draft you are editing and the revision answering
// the phone. Nothing here invents a number — a metric with no evidence behind it renders as a
// dash, because a confident-looking figure the platform cannot support is worse than a gap.

/** Every instance this agent has ever been: its published revisions, plus whatever is live.
 *  A saved call records the instance that held it, which is how a call finds its agent. */
export function instanceIds(agent: StudioAgent): Set<string> {
  const ids = new Set(agent.history.map((r) => r.id));
  if (agent.channel) ids.add(agent.channel.instance_id);
  return ids;
}

/** A rehearsal is a test against a synthetic calendar. It is not practice traffic and must
 *  never be counted as it — the whole point of the mode is that it has no real consequence. */
export const isRehearsal = (call: ConversationSummary) =>
  call.metadata?.mode === "rehearsal" || call.metadata?.calendar === "sandbox";

export interface CallMetrics {
  /** Live calls that started inside the window. */
  calls: number;
  /** Of those, the ones that reached a governed write at all. */
  attempts: number;
  /** Of those attempts, the ones the engine ruled `ok`. Not what the agent said. */
  booked: number;
  /** Calls the engine blocked or rejected, or where source state was unavailable. */
  refused: number;
  /** Median call length in seconds; null when no call has recorded an end. */
  medianSeconds: number | null;
}

const EMPTY: CallMetrics = {
  calls: 0,
  attempts: 0,
  booked: 0,
  refused: 0,
  medianSeconds: null,
};

export function callMetrics(
  calls: ConversationSummary[],
  days = 7,
): CallMetrics {
  const since = Date.now() - days * 86_400_000;
  const live = calls.filter(
    (c) => !isRehearsal(c) && new Date(c.started_at).getTime() >= since,
  );
  if (live.length === 0) return EMPTY;
  const durations = live
    .filter((c) => c.ended_at)
    .map(
      (c) =>
        (new Date(c.ended_at as string).getTime() -
          new Date(c.started_at).getTime()) /
        1000,
    )
    .filter((s) => s >= 0)
    .sort((a, b) => a - b);
  const mid = Math.floor(durations.length / 2);
  return {
    calls: live.length,
    attempts: live.filter((c) => c.outcome !== null).length,
    booked: live.filter((c) => c.outcome === "ok").length,
    refused: live.filter((c) => c.outcome !== null && c.outcome !== "ok").length,
    medianSeconds: durations.length
      ? durations.length % 2
        ? durations[mid]
        : Math.round((durations[mid - 1] + durations[mid]) / 2)
      : null,
  };
}

/** Share of booking attempts the engine let through, as a percentage — or null when nothing
 *  was attempted. Zero attempts is not zero percent; it is no answer. */
export const bookedShare = (m: CallMetrics): number | null =>
  m.attempts === 0 ? null : Math.round((m.booked / m.attempts) * 100);

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// --- draft vs. live ----------------------------------------------------------------------

/** One difference between the draft and the revision currently answering calls. */
export interface ConfigChange {
  /** Which editor section to open to see it, when we know of one. */
  section?: string;
  label: string;
  kind: "added" | "changed" | "removed";
  /** What changed, in a line — never the whole new value. */
  summary: string;
}

/** Config fields grouped the way the editor presents them, so a change points at the screen
 *  that produced it. Keys that move together (street/postcode/city) count as one change. */
const GROUPS: {
  keys: (keyof AgentConfig)[];
  label: string;
  section: string;
}[] = [
  { keys: ["name"], label: "Agent name", section: "instructions" },
  { keys: ["greeting"], label: "Greeting", section: "instructions" },
  { keys: ["instructions"], label: "Conversation style", section: "instructions" },
  { keys: ["practice_name"], label: "Practice name", section: "knowledge" },
  { keys: ["street", "postcode", "city"], label: "Address", section: "knowledge" },
  { keys: ["phone", "email"], label: "Practice contact", section: "knowledge" },
  { keys: ["timezone"], label: "Timezone", section: "knowledge" },
  { keys: ["weekdays"], label: "Opening days", section: "knowledge" },
  { keys: ["open_from", "open_until"], label: "Opening hours", section: "knowledge" },
  { keys: ["break_from", "break_until"], label: "Break", section: "knowledge" },
  { keys: ["slot_minutes"], label: "Appointment length", section: "knowledge" },
  { keys: ["treatments"], label: "Treatments", section: "knowledge" },
  { keys: ["booking_enabled"], label: "Booking", section: "capabilities" },
  { keys: ["consent_policy_id"], label: "Privacy policy", section: "capabilities" },
  { keys: ["recipients"], label: "Call summary recipients", section: "capabilities" },
  { keys: ["locale"], label: "Language", section: "voice" },
  { keys: ["voice_id"], label: "Voice", section: "voice" },
  { keys: ["resources"], label: "Calendar resources", section: "phone" },
];

const isEmpty = (v: unknown): boolean =>
  v === null ||
  v === undefined ||
  v === "" ||
  (Array.isArray(v) && v.length === 0);

const count = (v: unknown, label: string) =>
  `${(v as unknown[]).length} ${label.toLowerCase()}`;

function describe(
  keys: (keyof AgentConfig)[],
  label: string,
  draft: AgentConfig,
  live: AgentConfig,
): string {
  const [key] = keys;
  const a = live[key];
  const b = draft[key];
  if (typeof b === "boolean") return b ? `Turned on` : `Turned off`;
  if (Array.isArray(b)) return `${count(a, label)} → ${count(b, label)}`;
  if (keys.length > 1) return `${keys.length} fields edited`;
  const before = String(a ?? "");
  const after = String(b ?? "");
  // A greeting or a style paragraph does not fit on this line and does not need to — the
  // scale of the edit is what you want in a list; the text itself is one click away.
  if (before.length > 42 || after.length > 42)
    return `Edited · ${before.length} → ${after.length} characters`;
  return `${before || "—"} → ${after || "—"}`;
}

/** Everything the draft would change about the live agent if you published it now.
 *  An empty list means publishing is a no-op; no live revision means there is nothing to
 *  compare against, which is a different thing and the caller is told so by `live === null`. */
export function draftChanges(
  draft: AgentConfig,
  live: AgentConfig | null,
): ConfigChange[] {
  if (!live) return [];
  const changes: ConfigChange[] = [];
  for (const { keys, label, section } of GROUPS) {
    if (keys.every((k) => JSON.stringify(draft[k]) === JSON.stringify(live[k])))
      continue;
    const wasEmpty = keys.every((k) => isEmpty(live[k]));
    const isNowEmpty = keys.every((k) => isEmpty(draft[k]));
    changes.push({
      section,
      label,
      kind: wasEmpty ? "added" : isNowEmpty ? "removed" : "changed",
      summary: describe(keys, label, draft, live),
    });
  }
  // Anything GROUPS does not know about still has to surface. A config field added to the
  // backend before this table catches up would otherwise change the agent while the rail
  // reported "your draft matches the published revision" — the one sentence this panel must
  // never get wrong. So an unmapped key is reported by name rather than dropped.
  const covered = new Set(GROUPS.flatMap((g) => g.keys as string[]));
  for (const key of new Set([...Object.keys(draft), ...Object.keys(live)])) {
    if (covered.has(key)) continue;
    const a = live[key as keyof AgentConfig];
    const b = draft[key as keyof AgentConfig];
    if (JSON.stringify(a) === JSON.stringify(b)) continue;
    changes.push({
      label: key,
      kind: isEmpty(a) ? "added" : isEmpty(b) ? "removed" : "changed",
      summary: "Changed — this setting has no editor section yet",
    });
  }
  return changes;
}

export const CHANGE_MARK: Record<ConfigChange["kind"], string> = {
  added: "+",
  changed: "~",
  removed: "−",
};
