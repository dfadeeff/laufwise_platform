"""The StateProvider seam — the platform's most important integration surface.

Preconditions and postconditions are evaluated against a StateProvider, never against model
output. This Protocol is the stable boundary the platform programs against; it is *deliberately
identical* to laufwise's own `StateProvider` (`query(name, params=None) -> StateView`) so a
platform provider can be handed straight to the engine. Real providers (calendar in Stage 5,
PVS/FHIR/ERP later) implement this same shape.

A provider MUST surface "state unavailable" as a raised StateUnavailableError so the runtime can
BLOCK the step, rather than returning empty/partial state that a check might silently pass.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from laufwise.state.base import StateView


@runtime_checkable
class StateProvider(Protocol):
    def query(self, name: str, params: dict | None = None) -> StateView:
        """Return a read-only StateView snapshot for a named binding.

        Raises:
            StateUnavailableError: when the system of record cannot be read.
        """
        ...