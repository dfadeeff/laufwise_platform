"""Facade over the laufwise control-plane primitive.

This is the ONE place the platform binds to the engine. Everything above it (API,
workloads) depends on this facade, not on laufwise directly — so the engine can be
swapped (LocalEngine -> TemporalEngine) without touching the rest of the app.

laufwise is imported lazily inside methods so the rest of the backend boots and is
testable before the primitive is installed (`pip install -e ../../laufwise`).
"""

from __future__ import annotations

from app.core.errors import RuntimeNotConfiguredError
from app.schemas.run import RunRequest, RunResult


class Runtime:
    """Loads runbooks and drives runs through the enforced contract loop."""

    def __init__(self, runs_dir: str) -> None:
        self._runs_dir = runs_dir

    @staticmethod
    def _require_engine():
        """Import laufwise on demand, with a clear error if it isn't wired yet."""
        try:
            import laufwise  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised once wired
            raise RuntimeNotConfiguredError(
                "laufwise is not installed. Run: pip install -e ../../laufwise"
            ) from exc
        return laufwise

    def list_runbooks(self) -> list[str]:
        """Return the names of available runbooks. Stub until spec loading is wired."""
        return []

    def run(self, request: RunRequest) -> RunResult:
        """Execute a runbook against a case through the enforced loop.

        Wiring TODO: build a laufwise LocalEngine from the runbook spec + a StateProvider
        (app.providers) + an ExecutionAdapter (app.workloads), drive run_step over each
        step, and map StepResult/RunResult from the engine's outcomes.
        """
        self._require_engine()
        raise RuntimeNotConfiguredError("runtime.run is not wired yet (Phase 0)")