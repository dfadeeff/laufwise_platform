"""Resolve a deployed instance's bound connections into engine connectors (ADR-0003 wiring).

For each connection the instance binds, build the real StateProvider + tools the engine needs.
Only the thevea calendar connection is implemented; a `memory`/simulated connection contributes
nothing (the fixture fallback serves it). Credentials are decrypted **transiently** here and
never leave this layer.

`build_thevea_client` is a factory seam so tests can inject a mock HTTP transport without a real
thevea login.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from laufwise.state.base import StateProvider

from app.config import settings
from app.connections import crypto
from app.db import repo
from app.db.models import AgentInstance
from app.providers.thevea import TheveaClient, TheveaStateProvider
from app.workloads.thevea_tools import thevea_tools


def build_thevea_client(base_url: str, username: str, password: str) -> TheveaClient:
    """Factory seam — tests monkeypatch this to inject an httpx MockTransport."""
    return TheveaClient(base_url, username, password)


@dataclass
class Connectors:
    """The real providers + tools a run needs, plus closers for their clients."""

    providers: dict[str, StateProvider] = field(default_factory=dict)
    tools: dict[str, Callable[[Any, Any], Any]] = field(default_factory=dict)
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        for close in self._closers:
            try:
                close()
            except Exception:  # noqa: BLE001 — cleanup must never mask the run's result
                pass


async def resolve_connectors(
    session: AsyncSession, instance: AgentInstance, case: dict[str, Any]
) -> Connectors:
    """Build the connectors for an instance's bound connections. The requested appointment
    window travels in `case["calendar"]` and is handed to the thevea provider + book tool."""
    connectors = Connectors()
    window = (case or {}).get("calendar") or {}

    for binding in instance.connections:
        conn = await repo.get_connection(session, binding.connection_id, instance.tenant_id)
        if conn is None or conn.adapter != "thevea":
            continue  # simulated/memory connection -> handled by the fixture fallback

        creds = json.loads(crypto.decrypt(conn.tokens_enc)) if conn.tokens_enc else {}
        base_url = (conn.config or {}).get("base_url") or settings.thevea_base_url
        client = build_thevea_client(
            base_url, creds.get("username", ""), creds.get("password", "")
        )
        connectors.providers["thevea"] = TheveaStateProvider(client, window=window)
        connectors.tools.update(thevea_tools(client, window=window))
        connectors._closers.append(client.close)

    return connectors