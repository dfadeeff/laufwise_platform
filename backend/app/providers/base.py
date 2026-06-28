"""The StateProvider seam — the platform's most important integration surface.

Preconditions and postconditions are evaluated against a StateProvider, never against
model output. This Protocol is the stable boundary the platform programs against; the
control-plane facade binds it to laufwise's own StateProvider seam at runtime.

A provider MUST surface "state unavailable" as a raised StateUnavailableError so the
runtime can BLOCK the step, rather than returning empty/partial state that a check might
silently pass.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StateProvider(Protocol):
    def query(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return a read-only snapshot of state for a named binding.

        Raises:
            StateUnavailableError: when the system of record cannot be read.
        """
        ...