# Laufwise Platform

A **governed agent runtime**: the runtime that lets you run any agent inside an enforced,
auditable process contract grounded in your real systems of record. See
[PLATFORM_PLAN.md](PLATFORM_PLAN.md) for the thesis, architecture, and roadmap.

This repo is a monorepo with a clear backend/frontend split:

```
laufwise_platform/
├── backend/    # FastAPI — the control plane API, providers, workloads, infra layers
├── frontend/   # Next.js — the operator console (runs, episode logs, approvals, runbooks)
├── PLATFORM_PLAN.md
└── CLAUDE.md   # engineering rules for this repo
```

The control plane itself is the [`laufwise`](https://github.com/dfadeeff/laufwise) primitive,
consumed as a dependency by the backend (`backend/app/control_plane/`). The platform wraps it
with an HTTP API, identity, memory, observability, and the agent surfaces ("workloads").

## Quick start

Backend and frontend run independently. See each folder's `README.md`.

```bash
# backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload          # http://localhost:8000  (docs at /docs)

# frontend (in another terminal)
cd frontend && npm install
npm run dev                            # http://localhost:3000
```

Or with Docker: `docker compose up`.

## Architecture (one screen)

```
WORKLOADS (agents) — conversational | back-office task | document/workflow   ← peers
        │  every action passes through ▼
CONTROL PLANE (= laufwise)
  precond → tool allowlist → approval → execute → postcond → checkpoint + trace
        │            │              │              │
   IDENTITY      MEMORY        STATE (SoR)     OBSERV / AUDIT
```

The workload never calls tools directly — every action routes through a runbook step the
runtime enforces against the real system of record.