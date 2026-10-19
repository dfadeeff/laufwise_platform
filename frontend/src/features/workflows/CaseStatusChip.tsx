import type { CaseStatus } from "@/types";

const STATUS: Record<CaseStatus, { label: string; chip: string }> = {
  draft: { label: "Draft", chip: "border-border bg-muted text-muted-foreground" },
  blocked: { label: "Questions open", chip: "border-warning/25 bg-warning/10 text-warning" },
  ready: { label: "Ready", chip: "border-success/25 bg-success/10 text-success" },
  failed: { label: "Check failed", chip: "border-danger/25 bg-danger/10 text-danger" },
};

export function CaseStatusChip({ status }: { status: CaseStatus }) {
  const s = STATUS[status] ?? STATUS.draft;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${s.chip}`}
    >
      {s.label}
    </span>
  );
}
