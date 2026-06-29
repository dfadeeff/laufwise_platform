"""In-memory StateProvider backed by a JSON fixture (the v0 default).

Subclasses laufwise's own MemoryStateProvider so the platform's in-memory provider is, by
construction, conformant with the engine seam (`query(name, params=None) -> StateView`, plus
`apply()` so the SimulatedAdapter can stand in for a real system of record). Real providers
(calendar, PVS/FHIR, ERP/CRM) implement the same `StateProvider` Protocol from `base.py`.
"""

from __future__ import annotations

from laufwise.state.memory import MemoryStateProvider as _LaufwiseMemoryProvider


class MemoryStateProvider(_LaufwiseMemoryProvider):
    """Serves state from an in-process dict keyed by binding name (StateView per binding)."""