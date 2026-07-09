"""Import orchestration (ADR-0004 D4) — enumerate the source, run one governed contract per
appointment, aggregate a completeness report.

This sits ABOVE the engine: it lists the source work-list, then for each appointment starts a
governed run of the `calendar_import` contract (which re-grounds against the source and enforces
idempotent, verified, append-only copy). It classifies each run's outcome and reports
source-count vs. created + skipped + failed, so nothing is silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections import crypto
from app.connectors import base as connectors_base
from app.control_plane.runtime import Runtime
from app.core.errors import NotFoundError
from app.db import repo
from app.db.models import AgentInstance


@dataclass
class ImportReport:
    total: int = 0
    created: list[str] = field(default_factory=list)  # source refs newly appended
    skipped: list[str] = field(default_factory=list)  # already present (append-only skip)
    failed: list[dict[str, Any]] = field(default_factory=list)  # {ref, status, reason}

    @property
    def complete(self) -> bool:
        return self.total == len(self.created) + len(self.skipped) + len(self.failed)


async def _source_connector(session: AsyncSession, instance: AgentInstance) -> Any:
    """Build the source connector from the instance's `source` connection binding."""
    for binding in instance.connections:
        if binding.role != "source":
            continue
        conn = await repo.get_connection(session, binding.connection_id, instance.tenant_id)
        if conn is None:
            break
        creds = json.loads(crypto.decrypt(conn.tokens_enc)) if conn.tokens_enc else {}
        base_url = (conn.config or {}).get("base_url") or ""
        return connectors_base.build_connector(conn.adapter, base_url, creds)
    raise NotFoundError("instance has no bound source connection")


async def run_import(
    session: AsyncSession, runtime: Runtime, instance: AgentInstance, window: dict[str, Any]
) -> ImportReport:
    """List the source appointments for `window` and copy each through the governed contract."""
    source = await _source_connector(session, instance)
    try:
        appointments = source.list_appointments(window)
    finally:
        source.close()

    # Window filter: import only bookings whose start date falls in [from, to]. Then an optional
    # `limit` (import the first N) — both are the safety valves for a first real run.
    appointments = [a for a in appointments if _in_window(a, window)]
    params = instance.param_values or {}
    limit = _as_int(params.get("limit"))
    if limit:
        appointments = appointments[:limit]
    rooms = _rooms(params)  # balance created appointments across these room ids (round-robin)

    report = ImportReport(total=len(appointments))
    for i, appt in enumerate(appointments):
        case = {
            "appointment": {"ref": appt.ref, **appt.raw},
            "room_id": rooms[i % len(rooms)],
            "window": window,
        }
        result = await runtime.run_instance(session, instance, case)
        status = _overall(result)
        if status == "ok":
            report.created.append(appt.ref)
        elif status == "blocked":
            # The idempotency precondition blocked -> already in thevea -> a skip, not a failure.
            report.skipped.append(appt.ref)
        else:  # rejected | state_unavailable
            reason = next((s.reason for s in result.steps if s.reason), None)
            report.failed.append({"ref": appt.ref, "status": status, "reason": reason})
    return report


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rooms(params: dict[str, Any]) -> list[int]:
    """The destination room ids to balance appointments across (round-robin). From the instance's
    `rooms` param (comma-separated, e.g. "208413,208414"); defaults to MA 1 only."""
    raw = str(params.get("rooms") or "208413")
    ids = [_as_int(x) for x in raw.replace(" ", "").split(",") if x]
    return [i for i in ids if i] or [208413]


def _in_window(appt: Any, window: dict[str, Any]) -> bool:
    """Keep an appointment if its start date is within [from, to] (either bound optional).
    Dates compare on the YYYY-MM-DD prefix, so window bounds are plain dates."""
    lo, hi = window.get("from"), window.get("to")
    if not lo and not hi:
        return True
    day = (str(appt.start)[:10]) if appt.start else ""
    if not day:
        return True
    if lo and day < str(lo)[:10]:
        return False
    if hi and day > str(hi)[:10]:
        return False
    return True


def _overall(result: Any) -> str:
    """The run's overall status from its step results (worst-of)."""
    order = ["state_unavailable", "blocked", "rejected"]
    statuses = {s.status.value if hasattr(s.status, "value") else s.status for s in result.steps}
    for bad in order:
        if bad in statuses:
            return bad
    return "ok"