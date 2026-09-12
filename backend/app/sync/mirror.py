"""Mirror orchestration (ADR-0006 D3) — walk the days of the window, run one governed contract
per day, aggregate the same completeness report the import uses.

Like the import orchestrator this sits ABOVE the engine: it decides WHICH days to publish; the
contract decides whether each one may be written and whether it landed. Days are the unit, so the
report's refs are dates (`2026-09-14`), not appointment ids.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.runtime import Runtime
from app.db.models import AgentInstance
from app.sync.orchestrator import ImportReport, _overall, _rooms

# A window is picked in the studio as Today / Tomorrow / the next 7 days. The cap is a guard
# against a hand-typed range turning one press into hundreds of thevea reads, not a policy.
_MAX_DAYS = 62


async def run_mirror(
    session: AsyncSession,
    runtime: Runtime,
    instance: AgentInstance,
    window: dict[str, Any],
    on_progress: Callable[[ImportReport], Awaitable[None]] | None = None,
) -> ImportReport:
    """Publish each day in `window` through the governed contract.

    `on_progress` is awaited once the day count is known and again after each day — the hook the
    background worker uses to persist live progress to the job row.
    """
    days = _days(window)
    rooms = _rooms(instance.param_values or {})

    report = ImportReport()
    report.total = len(days)
    if on_progress:
        await on_progress(report)

    for day in days:
        result = await runtime.run_instance(session, instance, {"day": day, "rooms": rooms})
        status = _overall(result)
        if status == "ok":
            report.created.append(day)
        elif status == "blocked":
            # The precondition blocked -> the website already shows this day -> a skip.
            report.skipped.append(day)
        else:  # rejected | state_unavailable
            reason = next((s.reason for s in result.steps if s.reason), None)
            report.failed.append({"ref": day, "status": status, "reason": reason})
        if on_progress:
            await on_progress(report)
    return report


def _days(window: dict[str, Any]) -> list[str]:
    """Every calendar day in [from, to] as YYYY-MM-DD. An absent or unreadable bound yields no
    days at all — publishing "some default range" over the practice's live availability is the
    one thing this must never do on a malformed window."""
    lo, hi = str(window.get("from") or "")[:10], str(window.get("to") or "")[:10]
    try:
        first, last = date.fromisoformat(lo), date.fromisoformat(hi)
    except ValueError:
        return []
    if last < first:
        return []
    span = min((last - first).days, _MAX_DAYS - 1)
    return [(first + timedelta(days=i)).isoformat() for i in range(span + 1)]
