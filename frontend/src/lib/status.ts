// Shared status styling for run/step outcomes — used by the console (/runs) and the
// landing console preview. Plain data (no JSX) so it imports cleanly into both server
// and client components.

import type { StepStatus } from "@/types";

export const STATUS: Record<
  StepStatus,
  { label: string; chip: string; dot: string; glyph: string }
> = {
  ok: { label: "OK", chip: "border-success/20 bg-success/10 text-success", dot: "bg-success", glyph: "✓" },
  blocked: { label: "Blocked", chip: "border-warning/20 bg-warning/10 text-warning", dot: "bg-warning", glyph: "⊘" },
  rejected: { label: "Rejected", chip: "border-danger/20 bg-danger/10 text-danger", dot: "bg-danger", glyph: "✕" },
  pending: { label: "Pending", chip: "border-border bg-muted text-muted-foreground", dot: "bg-muted-foreground", glyph: "◷" },
};