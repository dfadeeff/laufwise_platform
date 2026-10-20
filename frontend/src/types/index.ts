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
export type AgentCategory = "operational" | "conversational";
export type AgentDriver = "workflow" | "conversation";
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
  category: AgentCategory;
  driver: AgentDriver;
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
  label?: string;
  rooms?: Record<string, number>;
  /** The calendar mapping under whichever key the connection's system uses. */
  mapping?: Record<string, string>;
  id: string;
  type: string;
  adapter: string;
  created_at: string;
  /** A mail account (type "email"): the address and server it sends through. Never the password. */
  email_from?: string;
  email_host?: string;
}

export interface ImportJob {
  job_id: string;
  task_id?: string | null;
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
  // Copies already in thevea that the source has since moved or cancelled — append-only skips
  // them, so the operator has to fix them by hand. Optional for an older backend.
  review?: ImportReviewItem[];
  patients?: Record<string, string>; // ref -> patient name, for the failed and forced refs only
  complete: boolean; // status === "completed"
  error?: string | null; // set only if the whole job crashed
}

export interface ImportReviewItem {
  kind: "moved" | "cancelled_in_source" | "unchecked";
  ref: string;
  patient?: string | null;
  source_start?: string; // ISO UTC
  dest_start?: string; // ISO UTC
  source_status?: string;
  reason?: string;
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
  config?: Record<string, unknown>;
}

/** A practice system the Studio can connect, straight from the backend registry (ADR-0014). */
export interface CalendarSystem {
  key: string;
  label: string;
  /** One request, or a login that may ask for an emailed code. */
  connect: "password" | "password_and_code";
  mapping: { config_key: string; label: string; numeric: boolean };
  /** A subset of availability, patients, booking: what a call on this system can do. */
  capabilities: string[];
}

/** A document the practice wrote for its agents (ADR-0017). */
/** What a caller can be booked for, as the workspace offers it (ADR-0025). */
export interface WorkspaceServiceIn {
  name: string;
  /** The calendar system's own id (Thevea: price-list position). Empty: not bookable by phone. */
  external_id: string;
  duration_minutes: number | null;
  /** How callers say it, per language. */
  aliases: { de?: string[]; en?: string[]; ru?: string[] };
  bookable: boolean;
  /** Offered when a caller cannot say what they need. */
  consultation: boolean;
  /** Keys of the agent's payment categories it may be booked with; empty means any (ADR-0027). */
  payment_categories: string[];
  /** Its row on the workspace's website price list, by name; empty: no website price (ADR-0032). */
  price_label: string;
}

/** One price as the practice website shows it. */
export interface PriceRow {
  label: string;
  text: string;
  amount: string;
  kind: "fixed" | "from" | "package";
  detail: string;
  seen_at: string;
  /** Found by the last successful read; false keeps its last known price. */
  on_page: boolean;
}

/** The workspace's website price list (ADR-0032). */
export interface PriceList {
  url: string;
  rows: PriceRow[];
  checked_at: string | null;
  attempted_at: string | null;
  error: string;
}

export interface WorkspaceService extends WorkspaceServiceIn {
  id: string;
  key: string;
  /** Switched on AND linked: the release gate, decided by the backend. */
  phone_bookable: boolean;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source: "text" | "pdf" | "url";
  chars: number;
  updated_at: string;
  /** Present when one document is read on its own. */
  content?: string;
}

/** A starting point for a new agent: a practice type's usual hours, appointment length and style. */
export interface PracticeType {
  key: string;
  label: string;
  description: string;
}

/** One workspace at a glance, for the agency overview. */
/** A module a workspace can run: one vertical's workflows and how the Studio speaks there
 *  (ADR-0028). `templates` are the runbooks it owns; `surfaces` the kernel features it turns on. */
export interface WorkspaceModule {
  key: string;
  label: string;
  summary: string;
  pitch: string[];
  intro: string;
  templates: string[];
  surfaces: string[];
}

export interface WorkspaceModules {
  running: WorkspaceModule[];
  available: WorkspaceModule[];
}

/** A file the workspace uploaded (ADR-0029). `media_type` is what the bytes are, checked on
 *  upload; the same content is stored once per workspace. */
export interface StoredFile {
  id: string;
  name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string | null;
}

/** The form dialect the kernel derives from a module's dataclasses (ADR-0030): objects, arrays,
 *  strings (dates and decimals by `format`), booleans, integers and enums. */
export interface FieldSchema {
  type: "object" | "array" | "string" | "boolean" | "integer";
  title?: string;
  format?: "date" | "decimal";
  enum?: { value: string; label: string }[];
  nullable?: boolean;
  properties?: Record<string, FieldSchema>;
  items?: FieldSchema;
}

/** A workflow a module offers; a workspace creates workflow agents from it. */
export interface WorkflowKind {
  key: string;
  label: string;
  summary: string;
  module?: string;
}

export type CaseStatus = "draft" | "blocked" | "ready" | "failed";

export interface CaseSummary {
  id: string;
  title: string;
  status: CaseStatus;
  input_revision: number;
  computed_revision: number | null;
  updated_at: string | null;
  created_at: string | null;
}

export interface WorkflowAgent {
  id: string;
  name: string;
  module: string;
  workflow: WorkflowKind;
  created_at: string | null;
}

export interface WorkflowAgentDetail extends WorkflowAgent {
  settings: Record<string, unknown>;
  settings_schema: FieldSchema | null;
  cases: CaseSummary[];
}

/** One piece of work of a workflow agent: files in, an input a person corrects, and the result
 *  the platform computed from it. There is no way to write `result` except by computing. */
export interface CaseDetail extends CaseSummary {
  agent: WorkflowAgent | null;
  schemas: { input: FieldSchema; result: FieldSchema } | null;
  input: Record<string, unknown>;
  rules_version: string | null;
  result: Record<string, unknown> | null;
  errors: string[];
  questions: string[];
  assumptions: string[];
  problems: string[];
  files: { id: string; name: string; media_type: string; size_bytes: number }[];
  events: { kind: string; created_at: string | null; revision?: number; status?: string; name?: string }[];
}

export interface WorkspaceSummary {
  name: string;
  agents: { id: string; name: string; published: boolean; live: boolean; phone_number: string | null }[];
  calendars: number;
  numbers: string[];
  calls_7d: number;
  callbacks_waiting: number;
  /** What still stands between the practice and a working phone line. */
  attention: string[];
}

/** The practice's phone numbers, and the pool numbers it could claim. */
export interface NumbersView {
  owned: string[];
  /** The owned numbers this practice claimed itself, and so may release. */
  releasable?: string[];
  available: { number: string; name: string }[];
  max: number;
  /** Why nothing is offered, when nothing is (provider not configured, unreachable). */
  unavailable_reason?: string | null;
}

export interface DoctolibLoginStatus {
  job_id: string;
  status: "starting" | "awaiting_code" | "done" | "failed";
  error?: string | null;
  connection_id?: string | null;
}

export interface InstanceSummary {
  agent_id?: string | null;
  instance_id: string;
  template: string;
  template_version: number;
  status: InstanceStatus;
  param_values: Record<string, unknown>;
  connections: Record<string, string>; // role -> connection id
  phone_number?: string | null;
  // The named schedule this instance is armed for, or null for manual-only (ADR-0010 D3).
  schedule?: string | null;
  created_at: string;
}

// --- conversations (the conversational tier's timeline) -----------------------------------

/** How the engine ruled on the call's last consequential action. `null` = it never attempted one. */
export type ConversationOutcome = "ok" | "blocked" | "rejected" | "state_unavailable" | null;

export interface ConversationSummary {
  conversation_id: string;
  instance_id: string;
  channel: string;
  direction: string;
  status: string;
  external_id: string | null;
  metadata: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
  turns: number;
  tool_calls: number;
  outcome: ConversationOutcome;
  /** The caller's first words — a call's nearest thing to a subject line. */
  opening: string | null;
}

/** One entry on the timeline. `kind` is "turn" (someone spoke) or "tool_call" (the agent acted). */
export interface ConversationEvent {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  events: ConversationEvent[];
}
