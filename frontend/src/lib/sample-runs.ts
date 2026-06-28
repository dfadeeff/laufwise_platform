// Sample console data — illustrative only, NOT from the backend (the engine is wired
// in Phase 0). The console reads this until `GET /api/v1/runs` exists; swap the import
// in the page for the real api client at that point. Kept here (data) separate from the
// UI (page) on purpose.

import type { ConsoleRun } from "@/types";

export const SAMPLE_RUNS: ConsoleRun[] = [
  {
    id: "run_9f2a1c",
    runbook: "praxis_appointment",
    status: "blocked",
    started: "2026-06-28 09:14:02",
    duration: "1.2s",
    trace: "runs/praxis_appointment/ep_034.jsonl",
    steps: [
      { id: "verify_patient", phase: "precondition", status: "ok" },
      { id: "triage_safety", phase: "precondition", status: "ok" },
      {
        id: "book_slot",
        phase: "precondition",
        status: "blocked",
        reason: "consent.telephone_handling == false",
        blockedTool: "book_appointment",
      },
    ],
  },
  {
    id: "run_7c41be",
    runbook: "vendor_onboarding",
    status: "rejected",
    started: "2026-06-28 09:02:47",
    duration: "3.4s",
    trace: "runs/vendor_onboarding/ep_211.jsonl",
    steps: [
      { id: "intake_validate", phase: "postcondition", status: "ok" },
      {
        id: "prepare_erp_draft",
        phase: "postcondition",
        status: "rejected",
        reason: "vendor.exists == false (verified against ERP after execute)",
        note: "agent reported success",
      },
    ],
  },
  {
    id: "run_5d8e02",
    runbook: "praxis_appointment",
    status: "ok",
    started: "2026-06-28 08:51:19",
    duration: "2.0s",
    trace: "runs/praxis_appointment/ep_033.jsonl",
    steps: [
      { id: "verify_patient", phase: "precondition", status: "ok" },
      { id: "triage_safety", phase: "precondition", status: "ok" },
      { id: "book_slot", phase: "approval", status: "ok", approved: true },
      { id: "book_slot", phase: "postcondition", status: "ok" },
    ],
  },
  {
    id: "run_3a90f7",
    runbook: "praxis_refill",
    status: "blocked",
    started: "2026-06-28 08:39:55",
    duration: "0.9s",
    trace: "runs/praxis_refill/ep_018.jsonl",
    steps: [
      { id: "verify_patient", phase: "precondition", status: "ok" },
      {
        id: "triage_safety",
        phase: "precondition",
        status: "blocked",
        reason: "red_flag detected → escalate to human immediately",
      },
    ],
  },
  {
    id: "run_22b7d9",
    runbook: "insolvency_review",
    status: "ok",
    started: "2026-06-28 08:20:08",
    duration: "11.7s",
    trace: "runs/insolvency_review/ep_007.jsonl",
    steps: [
      { id: "load_documents", phase: "precondition", status: "ok" },
      { id: "flag_vorgaenge", phase: "execute", status: "ok" },
      { id: "verify_citations", phase: "postcondition", status: "ok" },
    ],
  },
];