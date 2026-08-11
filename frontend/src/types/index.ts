// Shared types — mirror the backend wire models (app/schemas).

export type StepStatus = "ok" | "blocked" | "rejected" | "state_unavailable" | "pending";

export interface StepResult {
  step_id: string;
  status: StepStatus;
  reason?: string | null;
  expr?: string | null; // the failing predicate, surfaced by the engine
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

// ---------------------------------------------------------------------------
// Studio — templates & the contract body (mirror backend/app/templates/contract.py
// and backend/app/schemas/template.py). The contract is stored/returned in pydantic
// field-name form: `name` (not `template`), `expr`/`reason` (not `check`/`else`).
// ---------------------------------------------------------------------------

export type TemplateStatus = "draft" | "published";
export type StepKind = "trace" | "enforced";
export type AgentClass = "conversational" | "workflow";
export type ParameterType = "text" | "enum" | "bool" | "int";

export interface CheckDef {
  expr: string;
  reason?: string | null;
}

export interface StateBindingDef {
  provider: string;
  query?: string | null;
}

export interface ApprovalDef {
  required_when?: string | null;
  prompt?: string | null;
}

export interface ExecuteDef {
  adapter: string;
  tool?: string | null;
  args?: Record<string, unknown>;
  effect?: Record<string, unknown>;
}

export interface ParameterSpec {
  type: ParameterType;
  options?: unknown[] | null;
  default?: unknown;
  required?: boolean;
  label?: string | null;
}

export interface StepDef {
  id: string;
  kind: StepKind;
  description?: string;
  tools?: string[];
  preconditions?: CheckDef[];
  postconditions?: CheckDef[];
  approval?: ApprovalDef | null;
  execute?: ExecuteDef | null;
  agent?: string | null;
  on_fail?: string;
}

export interface TemplateContract {
  name: string;
  version?: number;
  risk: string;
  agent_class: AgentClass;
  agent_surface?: string | null;
  parameters: Record<string, ParameterSpec>;
  required_connections: string[];
  state: Record<string, StateBindingDef>;
  steps: StepDef[];
}

export interface TemplateSummary {
  name: string;
  version: number;
  status: TemplateStatus;
  agent_class: string;
  agent_surface?: string | null;
  risk: string;
  step_count: number;
  created_at: string;
}

export interface TemplateDetail extends TemplateSummary {
  contract: TemplateContract;
  parameters: Record<string, ParameterSpec>;
}

export interface PublishResult {
  name: string;
  version: number;
  status: string;
}

// ---------------------------------------------------------------------------
// Studio — instances (mirror backend/app/schemas/instance.py).
// ---------------------------------------------------------------------------

export type InstanceStatus = "draft" | "deployed" | "paused";

export interface DeployRequest {
  template: string;
  version?: number;
  param_values: Record<string, unknown>;
  connections?: Record<string, string>;
  phone_number?: string | null;
}

export interface ConnectionSummary {
  id: string;
  type: string;
  adapter: string;
  created_at: string;
}

export interface ImportJob {
  job_id: string;
  status: "running" | "completed" | "failed";
  total: number;
  done: number; // created + forced + skipped + failed, so far
  created: string[];
  skipped: string[];
  failed: { ref: string; status: string; reason?: string | null }[];
  excluded: { ref: string; reason: string }[];
  // Written past thevea's own working-hours check because every room refused (ADR-0005 D7).
  // Optional so a response from an older backend still parses.
  forced?: string[];
  complete: boolean; // status === "completed"
  error?: string | null; // set only if the whole job crashed
}

export interface ConnectionPreview {
  ok: boolean;
  count: number;
  raw: unknown;
  error?: string | null;
}

export interface ConnectionCreate {
  type?: string;
  adapter?: string;
  credentials: Record<string, string>;
  config?: Record<string, string>;
}

export interface DoctolibLoginStatus {
  job_id: string;
  status: "starting" | "awaiting_code" | "done" | "failed";
  error?: string | null;
  connection_id?: string | null;
}

export interface InstanceSummary {
  instance_id: string;
  template: string;
  template_version: number;
  status: InstanceStatus;
  param_values: Record<string, unknown>;
  connections: Record<string, string>; // role -> connection id
  phone_number?: string | null;
  created_at: string;
}