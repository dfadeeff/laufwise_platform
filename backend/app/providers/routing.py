"""RoutingStateProvider — dispatch each binding to its real StateProvider by provider name.

The engine hands every binding's declared `provider` to the state provider in `params`
(`LocalEngine._resolve_state`). This provider routes on it: a binding whose provider name has a
registered real StateProvider goes there; anything else falls back to the in-memory fixture.

**Anti-fabrication rule (ADR-0003 D4):** a binding routed to a real provider is NEVER served from
the fixture. If the real source is unreachable it raises `StateUnavailable` (which the engine
turns into a BLOCK / STATE_UNAVAILABLE), rather than letting a caller-supplied fixture stand in
for the real system of record. The fixture only serves bindings that have no real provider
(e.g. `provider: memory` in a demo template).
"""

from __future__ import annotations

from laufwise.state.base import StateProvider, StateUnavailable, StateView


class RoutingStateProvider:
    """Routes `query()` to a per-provider-name StateProvider, with an optional fixture fallback."""

    def __init__(
        self,
        real: dict[str, StateProvider],
        fallback: StateProvider | None = None,
    ) -> None:
        self._real = real
        self._fallback = fallback

    def query(self, name: str, params: dict | None = None) -> StateView:
        provider = (params or {}).get("provider")
        target = self._real.get(provider) if provider is not None else None
        if target is not None:
            # Real source: failures propagate as StateUnavailable — never fixture fallback.
            return target.query(name, params)
        if self._fallback is not None:
            return self._fallback.query(name, params)
        raise StateUnavailable(
            f"no state provider registered for {provider!r} (binding {name!r})"
        )