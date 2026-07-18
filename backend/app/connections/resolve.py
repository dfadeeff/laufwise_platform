"""Resolve a deployed instance's bound connections into engine connectors (ADR-0004 wiring).

A calendar-import instance binds two roles — `source` (read) and `destination` (read+create).
This resolves each bound Connection into a live connector, exposes their reads as the
`source`/`destination` state providers, and builds the `create_appointment` tool bound to both.
Credentials are decrypted transiently here and never leave this layer.

`build_connector` (from app.connectors.base) is the seam tests monkeypatch to inject a mock
transport without a real login.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from laufwise.state.base import StateProvider

from app.config import settings
from app.connections import crypto
from app.connectors import base as connectors_base
from app.db import repo
from app.db.models import AgentInstance
from app.providers.appointment import DestinationMatchProvider, SourceAppointmentProvider
from app.workloads.import_tools import import_tools

# Per-adapter default base URL when a connection doesn't override it in its config.
_DEFAULT_BASE_URL = {
    "thevea": lambda: settings.thevea_base_url,
    "healthyfeet": lambda: settings.healthyfeet_base_url,
    "doctolib": lambda: settings.doctolib_base_url,
}


@dataclass
class Connectors:
    providers: dict[str, StateProvider] = field(default_factory=dict)
    tools: dict[str, Callable[[Any, Any], Any]] = field(default_factory=dict)
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        for close in self._closers:
            try:
                close()
            except Exception:  # noqa: BLE001 — cleanup must never mask the run's result
                pass


def _source_opts(conn: Any, window: dict[str, Any]) -> dict[str, Any]:
    """Adapter-specific options for a SOURCE connector. doctolib needs the agenda ids to read
    (from the connection config, comma-separated) and the run window to date-bound its query;
    other sources (healthyfeet) take none."""
    if conn.adapter == "doctolib":
        return {
            "agenda_ids": (conn.config or {}).get("agenda_ids", ""),
            "window_from": window.get("from"),
            "window_until": window.get("to"),
        }
    return {}


def client_from_connection(conn: Any, **opts: Any) -> Any:
    creds = json.loads(crypto.decrypt(conn.tokens_enc)) if conn.tokens_enc else {}
    base_url = (conn.config or {}).get("base_url") or _DEFAULT_BASE_URL.get(
        conn.adapter, lambda: ""
    )()
    return connectors_base.build_connector(conn.adapter, base_url, creds, **opts)


async def resolve_connectors(
    session: AsyncSession, instance: AgentInstance, case: dict[str, Any]
) -> Connectors:
    """Build the source + destination connectors for an import run. The appointment being copied
    travels in `case["appointment"]["ref"]` and parameterises both providers + the tool."""
    connectors = Connectors()
    ref = str((case.get("appointment") or {}).get("ref", ""))
    # Per-appointment destination options set by the orchestrator: the assigned room and the
    # find/verify window. Only the destination connector consumes them.
    window = case.get("window") or {}
    dest_opts: dict[str, Any] = {"window_from": window.get("from"), "window_until": window.get("to")}
    if case.get("room_id") is not None:
        dest_opts["room_id"] = int(case["room_id"])
    if case.get("rooms"):
        # The full room set to SEARCH for idempotency — so an appointment already in any room is
        # found regardless of which room this run would write it to (room-independent dedup).
        dest_opts["search_room_ids"] = [int(r) for r in case["rooms"]]
    source = destination = None

    for binding in instance.connections:
        # Only the import roles resolve to real connectors; any other role (e.g. a demo template's
        # simulated `calendar`/`memory` connection) is left to the fixture fallback.
        if binding.role not in ("source", "destination"):
            continue
        conn = await repo.get_connection(session, binding.connection_id, instance.tenant_id)
        if conn is None or conn.adapter not in _DEFAULT_BASE_URL:
            continue
        opts = dest_opts if binding.role == "destination" else _source_opts(conn, window)
        client = client_from_connection(conn, **opts)
        connectors._closers.append(client.close)
        if binding.role == "source":
            source = client
            connectors.providers["source"] = SourceAppointmentProvider(client, ref)
        else:  # destination
            destination = client
            connectors.providers["destination"] = DestinationMatchProvider(client, ref)

    if source is not None and destination is not None:
        connectors.tools.update(import_tools(source, destination, ref))
    return connectors