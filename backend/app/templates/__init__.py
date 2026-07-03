"""Studio template contracts — the platform-side authoring model (ADR-0002).

A *template* is the full Studio artifact: the process contract (steps, conditions, tool
allowlists, approvals) plus the Studio-only concepts the engine must never know about —
`kind` (trace|enforced), `agent_class`, `agent_surface`, `parameters`, `required_connections`.

The engine (laufwise) stays domain-agnostic: `compiler.to_runbook_spec` down-compiles a
template to a laufwise `RunbookSpec` of *enforced steps only* (ADR-0002 #8). Trace steps are
surface markers and never reach the engine.
"""