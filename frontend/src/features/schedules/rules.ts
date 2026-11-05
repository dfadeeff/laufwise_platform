import type { ScheduleOccurrence, ScheduleRule } from "@/types";

// The 30-minute grid every schedule is on (ADR-0040 D4): the server validates and computes the
// times; this only offers the choices and says a rule in words.

export const SLOTS: string[] = Array.from({ length: 48 }, (_, i) =>
  `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`,
);

export const DAYS: { n: number; short: string }[] = [
  { n: 1, short: "Mon" },
  { n: 2, short: "Tue" },
  { n: 3, short: "Wed" },
  { n: 4, short: "Thu" },
  { n: 5, short: "Fri" },
  { n: 6, short: "Sat" },
  { n: 7, short: "Sun" },
];

export const INTERVALS: { minutes: number; label: string }[] = [
  { minutes: 30, label: "30 minutes" },
  { minutes: 60, label: "hour" },
  { minutes: 120, label: "2 hours" },
  { minutes: 180, label: "3 hours" },
  { minutes: 240, label: "4 hours" },
  { minutes: 360, label: "6 hours" },
  { minutes: 720, label: "12 hours" },
];

const WEEKDAYS = [1, 2, 3, 4, 5];
const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export function describeDays(days?: number[]): string {
  const d = [...(days ?? [1, 2, 3, 4, 5, 6, 7])].sort();
  if (d.length === 7) return "Every day";
  if (same(d, WEEKDAYS)) return "Weekdays";
  if (same(d, [6, 7])) return "Weekends";
  return d.map((n) => DAYS[n - 1]?.short ?? n).join(", ");
}

/** A rule in words: "Weekdays at 08:00, 13:00 and 18:00". */
export function describeRule(rule: ScheduleRule): string {
  const days = describeDays(rule.days);
  if (rule.kind === "times") {
    const at = [...new Set(rule.at)].sort();
    const list = at.length > 1 ? `${at.slice(0, -1).join(", ")} and ${at[at.length - 1]}` : at[0];
    return `${days} at ${list}`;
  }
  const every = INTERVALS.find((i) => i.minutes === rule.minutes)?.label ?? `${rule.minutes} minutes`;
  const window =
    (rule.from ?? "00:00") === "00:00" && (rule.to ?? "23:30") === "23:30"
      ? ""
      : `, ${rule.from ?? "00:00"}–${rule.to ?? "23:30"}`;
  return `${days}, every ${every}${window}`;
}

/** A moment, as the clock in the schedule's timezone shows it. */
export function when(iso: string | null | undefined, timezone: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export const OCCURRENCE: Record<ScheduleOccurrence["status"], { label: string; chip: string }> = {
  queued: { label: "Queued", chip: "border-border bg-muted text-muted-foreground" },
  claimed: { label: "Running", chip: "border-primary/25 bg-primary/10 text-primary" },
  succeeded: { label: "Ran", chip: "border-success/25 bg-success/10 text-success" },
  failed: { label: "Failed", chip: "border-danger/25 bg-danger/10 text-danger" },
  skipped_busy: { label: "Skipped: still running", chip: "border-warning/25 bg-warning/10 text-warning" },
  skipped_missed: { label: "Missed", chip: "border-warning/25 bg-warning/10 text-warning" },
  cancelled: { label: "Cancelled", chip: "border-border bg-muted text-muted-foreground" },
};

/** The case a run started, if it started one. */
export function caseOf(runRef: string | null): string | null {
  return runRef?.startsWith("case:") ? runRef.slice(5) : null;
}
