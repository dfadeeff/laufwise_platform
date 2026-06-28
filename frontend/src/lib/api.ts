// The only module that talks to the backend control-plane API.

import type { Health, RunResult } from "@/types";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/health"),
  runbooks: () => get<string[]>("/runbooks"),
  approvals: () => get<unknown[]>("/approvals"),
  // Runs are created via POST; left for Phase 0 when the engine is wired.
  _baseUrl: BASE_URL,
};

export type { Health, RunResult };
