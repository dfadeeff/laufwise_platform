"""The ExecutionAdapter seam — the only place a model acts.

A workload (conversational, back-office task, document) wires its agent behind this
Protocol. The runtime calls `execute` for a step and passes the tool allowlist; the
adapter MUST refuse any tool call outside it. The allowlist is enforced here AND
re-asserted by the engine (defense in depth). Bound to laufwise's ExecutionAdapter at
runtime.
"""

from __future__ import annotations

from typing import Any, Protocol


class ExecutionAdapter(Protocol):
    def execute(
        self,
        step_id: str,
        context: dict[str, Any],
        allowed_tools: list[str],
    ) -> dict[str, Any]:
        """Run the step's work, confined to `allowed_tools`. Return the outcome."""
        ...