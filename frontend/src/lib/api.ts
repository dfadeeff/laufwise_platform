// The only module that talks to the backend control-plane API.

import type {
  DeployRequest,
  Health,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const auth = await authHeader();
  const res = await fetch(`${BASE_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: { ...auth, ...init?.headers },
  });
  if (!res.ok) throw await toApiError(res, init?.method ?? "GET", path);
  return res.json() as Promise<T>;
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

  // Studio — configuration tier (instances).
  listInstances: () => get<InstanceSummary[]>("/instances"),
  deployInstance: (req: DeployRequest) => post<InstanceSummary>("/instances", req),
  pauseInstance: (id: string) => post<InstanceSummary>(`/instances/${id}/pause`),
  runInstance: (id: string, caseFixture: Record<string, unknown>) =>
    post<RunResult>(`/instances/${id}/runs`, { case: caseFixture }),

  _baseUrl: BASE_URL,
};

export type { Health, RunResult };