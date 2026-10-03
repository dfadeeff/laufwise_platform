import type { AgentConfig, StudioAgent } from "@/features/agents/types";
// The only module that talks to the backend control-plane API.

import type {
  CalendarSystem,
  ConnectionCreate,
  KnowledgeDocument,
  NumbersView,
  PracticeType,
  WorkspaceSummary,
  ConversationDetail,
  ConversationSummary,
  ConnectionPreview,
  ConnectionSummary,
  DoctolibLoginStatus,
  DeployRequest,
  Health,
  ImportJob,
  InstanceSummary,
  PublishResult,
  RunResult,
  TemplateContract,
  TemplateDetail,
  TemplateSummary,
} from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  (process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/api/v1`
    : "http://127.0.0.1:8000/api/v1");

/** A non-2xx API response. For 422s from the publish/deploy gates, `violations`
 *  carries the gate's precise messages — render each one verbatim. */
export class ApiError extends Error {
  status: number;
  violations: string[];

  constructor(message: string, status: number, violations: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.violations = violations;
  }
}

async function toApiError(res: Response, method: string, path: string): Promise<ApiError> {
  let message = `${method} ${path} -> ${res.status}`;
  let violations: string[] = [];
  try {
    const body: unknown = await res.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; violations?: unknown };
      if (typeof d.message === "string") message = d.message;
      if (Array.isArray(d.violations)) violations = d.violations.map(String);
    }
  } catch {
    // non-JSON body — keep the fallback message
  }
  return new ApiError(message, res.status, violations);
}

// Clerk exposes the active session on window once ClerkProvider mounts. The session token
// (a short-lived JWT, cached by Clerk and only refreshed near expiry) carries the user + active
// org (org = tenant); the backend verifies it and scopes every query. No token (SSR, signed out,
// or Clerk not ready) -> the request still goes through and the backend applies its auth rules.
//
// This is deliberately defensive: token retrieval must NEVER block or crash a request. A Clerk
// hiccup (e.g. a session-refresh loop from a stale cookie) must not freeze the whole app, so the
// call is guarded, capped by a timeout, and any failure falls back to sending no token.
const TOKEN_TIMEOUT_MS = 2500;
// How long a request waits for Clerk to restore the session on a fresh page load.
const CLERK_LOAD_TIMEOUT_MS = 5000;

async function authHeader(): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {};
  try {
    type ClerkLike = {
      loaded?: boolean;
      session?: { getToken(): Promise<string | null> } | null;
    };
    const clerkNow = () => (window as unknown as { Clerk?: ClerkLike }).Clerk;
    // Wait for Clerk to finish restoring the session. A page loaded directly (a bookmark, a
    // refresh) fires its requests before Clerk has loaded, and sending those without a token
    // gets a 401 — or, on a backend that allowed it, someone else's workspace.
    const started = Date.now();
    while (!clerkNow()?.loaded && Date.now() - started < CLERK_LOAD_TIMEOUT_MS) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const clerk = clerkNow();
    if (!clerk?.session) return {}; // signed out (or Clerk never loaded) -> no token
    const token = await Promise.race([
      clerk.session.getToken(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), TOKEN_TIMEOUT_MS)),
    ]).catch(() => null);
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

// A hung request must surface as an error with a retry, never an infinite spinner. This caps
// every call so a slow/stuck backend or auth layer can't leave the UI loading forever.
const REQUEST_TIMEOUT_MS = 12000;

async function request<T>(path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const auth = await authHeader();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
      signal: controller.signal,
      headers: { ...auth, ...init?.headers },
    });
    if (!res.ok) throw await toApiError(res, init?.method ?? "GET", path);
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(`request timed out after ${REQUEST_TIMEOUT_MS}ms: ${path}`, 0);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

const get = <T>(path: string) => request<T>(path);
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/** The one schedule a process actually runs (`app/sync/scheduler.py`). */
export const MIRROR_SCHEDULE = "mirror";

export const api = {
  listRuns: () => get<Array<{run_id:string;runbook:string;version:number;status:string;started_at:string}>>("/runs"),
  getRun: (id:string) => get<{run_id:string;runbook:string;steps:import("@/types").StepResult[]}>(`/runs/${id}`),
  listFollowups: () => get<Array<{task_id:string;status:string;context:{conversation_id?:string;reason?:string;assigned_to?:string}}>>("/tasks"),
  updateFollowup: (id:string, action:"claim"|"complete") => post(`/tasks/${id}/followup`, {action}),
  listAgents: () => get<StudioAgent[]>("/agents"),
  createAgent: (seed?: {
    name: string;
    practice_name: string;
    locale: AgentConfig["locale"];
    practice_type?: string | null;
  }) => post<StudioAgent>("/agents", seed),
  listPracticeTypes: () => get<PracticeType[]>("/agents/practice-types"),
  // Studio — the workspace's documents for its agents (ADR-0017).
  listKnowledge: () =>
    get<{ documents: KnowledgeDocument[]; max_agent_chars: number }>("/knowledge"),
  addKnowledgeText: (title: string, content: string) =>
    post<KnowledgeDocument>("/knowledge", { title, content }),
  addKnowledgePdf: (title: string, data_base64: string) =>
    post<KnowledgeDocument>("/knowledge/pdf", { title, data_base64 }),
  getKnowledge: (id: string) => get<KnowledgeDocument>(`/knowledge/${id}`),
  // A page of the practice's website, saved as a document it can review and edit (ADR-0019).
  addKnowledgeUrl: (url: string, title = "") =>
    post<KnowledgeDocument>("/knowledge/url", { url, title }),
  updateKnowledge: (id: string, title: string, content: string) =>
    put<KnowledgeDocument>(`/knowledge/${id}`, { title, content }),
  deleteKnowledge: (id: string) => request<{ deleted: string }>(`/knowledge/${id}`, { method: "DELETE" }),
  // One workspace's summary, read with a token Clerk issued for THAT organization — so the agency
  // overview sees exactly the workspaces the login belongs to, without switching between them.
  workspaceSummary: (token: string) =>
    request<WorkspaceSummary>("/workspace/summary", { headers: { Authorization: `Bearer ${token}` } }),
  getAgent: (id: string) => get<StudioAgent>(`/agents/${id}`),
  listCapabilities: () => get<import("@/features/agents/types").AgentCapability[]>("/agents/capabilities"),
  forgetCallers: (id: string) =>
    request<{ forgotten: number }>(`/agents/${id}/callers`, { method: "DELETE" }),
  saveAgent: (id: string, generation: number, config: AgentConfig) => post<StudioAgent>(`/agents/${id}/draft`, { generation, config }),
  publishAgent: (id: string, generation: number) => post<StudioAgent>(`/agents/${id}/publish`, { generation }),
  pauseAgent: (id: string) => post<StudioAgent>(`/agents/${id}/pause`),
  activateAgent: (id: string, instance_id: string, connection_id: string, phone_number: string) => post<StudioAgent>(`/agents/${id}/activate`, { instance_id, connection_id, phone_number }),
  checkAgentConnection: (id: string, connection_id: string) => post<{ok: boolean; message: string}>(`/agents/${id}/check`, {connection_id}),
  health: () => get<Health>("/health"),
  runbooks: () => get<string[]>("/runbooks"),
  approvals: () => get<unknown[]>("/approvals"),

  // Studio — authoring tier (templates).
  listTemplates: () => get<TemplateSummary[]>("/templates"),
  getTemplate: (name: string, version?: number) =>
    get<TemplateDetail>(
      `/templates/${encodeURIComponent(name)}${version !== undefined ? `?version=${version}` : ""}`,
    ),
  saveDraft: (contract: TemplateContract) => post<TemplateDetail>("/templates", { contract }),
  publishTemplate: (name: string) =>
    post<PublishResult>(`/templates/${encodeURIComponent(name)}/publish`),

  // Studio — connections (a tenant's real systems of record; credentials encrypted server-side).
  listConnections: () => get<ConnectionSummary[]>("/connections"),
  listCalendarSystems: () => get<CalendarSystem[]>("/connections/systems"),
  // Studio — phone numbers claimed from the platform's pool (wired to the agent automatically).
  listNumbers: () => get<NumbersView>("/numbers"),
  claimNumber: (number: string) => post<NumbersView>("/numbers/claim", { number }),
  releaseNumber: (number: string) => post<NumbersView>("/numbers/release", { number }),
  createConnection: (req: ConnectionCreate) => post<ConnectionSummary>("/connections", req),
  previewConnection: (id: string) => post<ConnectionPreview>(`/connections/${id}/preview`),
  // Wipes the stored login and hides the connection; refused while something uses it.
  removeConnection: (id: string) =>
    request<{ removed: string }>(`/connections/${id}`, { method: "DELETE" }),
  // doctolib two-step connect: start a server-side headless login, poll it, deliver the emailed
  // code. The connection is created only once the login succeeds (status "done", connection_id set).
  startDoctolibLogin: (req: {
    username: string;
    password: string;
    agenda_ids?: string;
    label?: string;
    agendas?: Record<string, string>;
  }) =>
    post<DoctolibLoginStatus>("/connections/doctolib/login", req),
  pollDoctolibLogin: (jobId: string) =>
    get<DoctolibLoginStatus>(`/connections/doctolib/login/${jobId}`),
  submitDoctolibCode: (jobId: string, code: string) =>
    post<DoctolibLoginStatus>(`/connections/doctolib/login/${jobId}/code`, { code }),

  // Studio — configuration tier (instances).
  listInstances: () => get<InstanceSummary[]>("/instances"),
  deployInstance: (req: DeployRequest) => post<InstanceSummary>("/instances", req),
  pauseInstance: (id: string) => post<InstanceSummary>(`/instances/${id}/pause`),
  // Arm an instance for the backend clock, or disarm it with null (ADR-0010). The schedule MOVES:
  // arming one disarms whatever else this tenant had armed for the same name, so a redeploy
  // cannot leave the old instance running with the settings it was meant to replace.
  setInstanceSchedule: (id: string, schedule: string | null) =>
    put<InstanceSummary>(`/instances/${id}/schedule`, { schedule }),
  runInstance: (id: string, caseFixture: Record<string, unknown>) =>
    post<RunResult>(`/instances/${id}/runs`, { case: caseFixture }),
  // Import is a background job: POST starts it and returns immediately with a running job; the
  // client polls getImportJob for progress. No long request timeout needed — each call is quick.
  startImport: (id: string) =>
    post<ImportJob>(`/instances/${id}/import`),
  getImportJob: (id: string, jobId: string) =>
    get<ImportJob>(`/instances/${id}/import/${jobId}`),

  // Studio — short-lived media URL. Provider credentials remain server-side. The conversation is
  // opened server-side before any audio, so its id comes back with the socket URL.
  // `calendar` chooses what a test call uses (ADR-0018): the sandbox, or the practice's real
  // calendar read-only, or writing labelled test appointments (which needs an explicit yes).
  startVoiceSession: (
    language: "de" | "en" | "ru" | "ar",
    agent_id?: string,
    generation?: number,
    calendar?: {
      calendar_mode: "sandbox" | "read" | "write";
      connection_id?: string;
      confirm_real_writes?: boolean;
    },
  ) =>
    post<{ ws_url: string; conversation_id: string }>("/conversational/sessions", {
      language,
      agent_id,
      generation,
      ...calendar,
    }),

  // Studio — saved calls. The timeline the conversational tier writes as it talks.
  listConversations: () => get<ConversationSummary[]>("/conversations"),
  getConversation: (id: string) => get<ConversationDetail>(`/conversations/${id}`),

  /** One saved call as Markdown, to keep as an example.
   *
   * Not `request<T>()`: that parses JSON, and this is a file. Same auth header, same tenant
   * scoping on the server — an id you do not own is a 404 here exactly as it is everywhere else.
   */
  exportConversation: async (id: string): Promise<string> => {
    const res = await fetch(`${BASE_URL}/conversations/${id}/export`, {
      headers: await authHeader(),
    });
    if (!res.ok) throw await toApiError(res, "GET", `/conversations/${id}/export`);
    return res.text();
  },

  _baseUrl: BASE_URL,
};

export type { Health, RunResult };
