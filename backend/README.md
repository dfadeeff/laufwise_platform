# Backend — control plane API (FastAPI)

## Layout (organized by concern, not by file type)

```
app/
├── main.py            # app factory: middleware, routers, lifespan
├── config.py          # typed settings (pydantic-settings)
├── api/               # HTTP layer ONLY — request/response, no business logic
│   ├── deps.py        # shared FastAPI dependencies (runtime, identity, ...)
│   └── v1/            # versioned routers: health, runbooks, runs, approvals
├── control_plane/     # facade over the `laufwise` engine (the runtime spine)
├── providers/         # StateProvider implementations = the systems of record
├── workloads/         # agent surfaces that plug into the runtime (conversational, ...)
├── identity/          # tenant / actor (agent acting-as) / consent scope  [minimal]
├── memory/            # cross-run continuity built from episode logs       [scoped]
├── observability/     # OTEL trace sink wiring
├── schemas/           # pydantic wire models (DTOs) shared across the API
└── core/              # cross-cutting: logging, error types
```

**Dependency rule (inward only):** `api → control_plane/workloads → providers/identity/...`.
The API layer holds no business logic; the control plane never imports the API.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Wire the control-plane primitive (sibling repo) when ready:
pip install -e ../../laufwise

uvicorn app.main:app --reload     # http://localhost:8000/docs
pytest                            # tests
```

## Where to build next

- `control_plane/runtime.py` — bind to laufwise's `LocalEngine` (lazy-imported today).
- `providers/` — add a real StateProvider (calendar, PVS/FHIR, ERP) implementing the protocol.
- `workloads/conversational/` — mount the voice/chat surface (voice_agents pattern).