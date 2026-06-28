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

// Richer shapes used by the operator console (master-detail Runs view).
export type StepPhase =
  | "precondition"
  | "tool allowlist"
  | "approval"
  | "execute"
  | "postcondition";

export interface ConsoleStep {
  id: string;
  phase: StepPhase;
  status: StepStatus;
  reason?: string; // why it blocked / rejected
  blockedTool?: string; // tool refused by the allowlist / precondition
  note?: string; // e.g. "agent claimed success"
  approved?: boolean; // passed an approval gate
}

export interface ConsoleRun {
  id: string;
  runbook: string;
  status: StepStatus; // overall outcome (ok | blocked | rejected | pending)
  started: string;
  duration: string;
  trace: string;
  steps: ConsoleStep[];
}