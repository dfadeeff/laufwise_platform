// Shared types — mirror the backend wire models (app/schemas).

export type StepStatus = "ok" | "blocked" | "rejected" | "pending";

export interface StepResult {
  step_id: string;
  status: StepStatus;
  reason?: string | null;
  blocked_tool?: string | null;
}

export interface RunResult {
  run_id: string;
  runbook: string;
  steps: StepResult[];
  trace_path?: string | null;
}

export interface Health {
  status: string;
  version: string;
}