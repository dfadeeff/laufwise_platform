"""Memory seam — continuity across runs for the same subject (e.g. a returning caller).

Deliberately scoped: this is NOT a general memory store. It reads from the episode logs
the runtime already emits and exposes "what happened before for this subject" so a
workload can resume context. Implementations stay thin.
"""

from __future__ import annotations

from typing import Any, Protocol


class MemoryStore(Protocol):
    def recall(self, subject_id: str) -> list[dict[str, Any]]:
        """Return prior run summaries for a subject, most recent first."""
        ...

    def remember(self, subject_id: str, summary: dict[str, Any]) -> None:
        """Persist a summary of the current run for future recall."""
        ...