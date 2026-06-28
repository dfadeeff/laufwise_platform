"""In-memory StateProvider backed by a JSON fixture (the v0 default).

Mirrors laufwise's MemoryStateProvider so local runs need no external system of record.
Real providers (calendar, PVS/FHIR, ERP/CRM) implement the same Protocol.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import StateUnavailableError


class MemoryStateProvider:
    """Serves state from an in-process dict keyed by binding name."""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self._fixture = fixture

    def query(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name not in self._fixture:
            raise StateUnavailableError(f"no state bound for '{name}'")
        return self._fixture[name]