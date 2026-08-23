// The only module that talks to the backend control-plane API.

import type {
  ConnectionCreate,
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

async function authHeader(): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {};
  try {
    const clerk = (
      window as unknown as { Clerk?: { session?: { getToken(): Promise<string | null> } } }
    ).Clerk;
    if (!clerk?.session) return {}; // no active session -> no token, no getToken() call
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
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
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
  createConnection: (req: ConnectionCreate) => post<ConnectionSummary>("/connections", req),
  previewConnection: (id: string) => post<ConnectionPreview>(`/connections/${id}/preview`),
  // doctolib two-step connect: start a server-side headless login, poll it, deliver the emailed
  // code. The connection is created only once the login succeeds (status "done", connection_id set).
  startDoctolibLogin: (req: { username: string; password: string; agenda_ids: string }) =>
    post<DoctolibLoginStatus>("/connections/doctolib/login", req),
  pollDoctolibLogin: (jobId: string) =>
    get<DoctolibLoginStatus>(`/connections/doctolib/login/${jobId}`),
  submitDoctolibCode: (jobId: string, code: string) =>
    post<DoctolibLoginStatus>(`/connections/doctolib/login/${jobId}/code`, { code }),

  // Studio — configuration tier (instances).
  listInstances: () => get<InstanceSummary[]>("/instances"),
  deployInstance: (req: DeployRequest) => post<InstanceSummary>("/instances", req),
  pauseInstance: (id: string) => post<InstanceSummary>(`/instances/${id}/pause`),
  runInstance: (id: string, caseFixture: Record<string, unknown>) =>
    post<RunResult>(`/instances/${id}/runs`, { case: caseFixture }),
  // Import is a background job: POST starts it and returns immediately with a running job; the
  // client polls getImportJob for progress. No long request timeout needed — each call is quick.
  startImport: (id: string) =>
    post<ImportJob>(`/instances/${id}/import`),
  getImportJob: (id: string, jobId: string) =>
    get<ImportJob>(`/instances/${id}/import/${jobId}`),

  // Studio — short-lived media URL. Provider credentials remain server-side.
  startVoiceSession: () =>
    post<{ ws_url: string }>("/conversational/sessions"),

  _baseUrl: BASE_URL,
};

export type { Health, RunResult };
