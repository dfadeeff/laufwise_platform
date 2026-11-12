"""Doctolib block mirror orchestration (ADR-0044) — walk the days of the window and make each
day's doctolib blocks match thevea, through one governed contract per day.

`write` is the instance's switch. Until it is "yes" nothing is written: each day is only planned,
from the same reads and the same `plan_day` the contract uses, and the report says per day which
blocks it would set and remove. That is how the practice checks it before it touches doctolib.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.resolve import client_from_connection
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import AgentInstance
from app.providers.blocks import Plan, plan_day
from app.providers.doctolib import DoctolibError
from app.providers.thevea import TheveaError
from app.sync.block_ledger import DbBlockLedger
from app.sync.mirror import _days
from app.sync.orchestrator import ImportReport, _overall

_BERLIN = ZoneInfo("Europe/Berlin")


def pairs_param(params: dict[str, Any]) -> dict[int, str]:
    """`room:agenda,room:agenda` as {room: agenda}. A malformed pair is dropped, never guessed."""
    pairs: dict[int, str] = {}
    for item in str(params.get("pairs") or "").replace(" ", "").split(","):
        room, _, agenda = item.partition(":")
        if room.isdigit() and agenda.isdigit():
            pairs[int(room)] = agenda
    return pairs


def describe(plan: Plan) -> str:
    """One day's plan in the practice's own times, e.g. `+ 2570190 09:00–10:30; − 2557171 14:00–15:00`."""

    def span(block: Any) -> str:
        start, end = (
            datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(_BERLIN).strftime("%H:%M")
            for t in (block.start, block.end)
        )
        return f"{block.agenda} {start}–{end}"

    parts = [f"+ {span(b)}" for b in plan.create] + [f"− {span(b)}" for _, b in plan.delete]
    return "would " + ("set and remove: " if plan.delete else "set: ") + "; ".join(parts)


async def run_block_mirror(
    session: AsyncSession,
    runtime: Runtime,
    instance: AgentInstance,
    window: dict[str, Any],
    on_progress: Callable[[ImportReport], Awaitable[None]] | None = None,
) -> ImportReport:
    params = instance.param_values or {}
    days, pairs = _days(window), pairs_param(params)
    report = ImportReport()
    if not pairs:
        report.total = 1
        report.failed.append({"ref": "pairs", "status": "rejected", "reason": "no room:agenda pair set"})
        return report
    if str(params.get("write") or "no").strip().lower() != "yes":
        return await _preview(session, instance, days, pairs, report, on_progress)

    report.total = len(days)
    if on_progress:
        await on_progress(report)
    case_pairs = {str(room): agenda for room, agenda in pairs.items()}
    for day in days:
        result = await runtime.run_instance(session, instance, {"day": day, "pairs": case_pairs})
        status = _overall(result)
        if status == "ok":
            report.created.append(day)
        elif status == "blocked":
            report.skipped.append(day)  # already in sync
        else:
            reason = next((s.reason for s in result.steps if s.reason), None)
            report.failed.append({"ref": day, "status": status, "reason": reason})
        if on_progress:
            await on_progress(report)
    return report


async def _preview(
    session: AsyncSession, instance: AgentInstance, days: list[str], pairs: dict[int, str],
    report: ImportReport, on_progress: Callable[[ImportReport], Awaitable[None]] | None,
) -> ImportReport:
    """Plan every day and write nothing: a day already right is `skipped`, a day that would
    change is `excluded` with what it would do, an unreadable day is `failed`."""
    connections = {}
    for binding in instance.connections:
        if binding.role in ("occupancy", "blocks"):
            connections[binding.role] = await repo.get_connection(
                session, binding.connection_id, instance.tenant_id
            )
    if not connections.get("occupancy") or not connections.get("blocks"):
        report.total = 1
        report.failed.append({"ref": "connections", "status": "rejected",
                              "reason": "bind both thevea (occupancy) and doctolib (blocks)"})
        return report
    occupancy = client_from_connection(connections["occupancy"])
    blocks = client_from_connection(connections["blocks"])
    ledger = DbBlockLedger(instance.tenant_id, connections["blocks"].id, asyncio.get_running_loop())
    try:
        for day in days:
            try:
                plan = await asyncio.to_thread(plan_day, occupancy, blocks, ledger, day, pairs)
            except (TheveaError, DoctolibError) as exc:
                report.failed.append({"ref": day, "status": "state_unavailable", "reason": str(exc)})
                report.total += 1
            else:
                if plan.in_sync:
                    report.skipped.append(day)
                    report.total += 1
                else:  # not attempted: a preview writes nothing
                    report.excluded.append({"ref": day, "reason": describe(plan)})
            if on_progress:
                await on_progress(report)
    finally:
        occupancy.close()
        blocks.close()
    return report
