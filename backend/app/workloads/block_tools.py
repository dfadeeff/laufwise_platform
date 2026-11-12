"""The doctolib block mirror's write tool (ADR-0044) — make one day's blocks match thevea.

Creates the blocks that are missing first and deletes the stale ones after, so a time that moves
is never open in between. Every block is recorded in the ledger the moment doctolib returns its
id, so a run cut off half-way still knows what it created. It deletes only blocks from the ledger:
one the practice set by hand is invisible to it.

Like every tool here it only CLAIMS success; the step's postcondition re-plans from fresh reads
of both systems and decides.
"""

from __future__ import annotations

from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.connectors.base import BlockCalendar, OccupancySource
from app.providers.blocks import NOTES, TITLE, BlockLedger, plan_day
from app.providers.doctolib import DoctolibError
from app.providers.thevea import TheveaError


def block_tools(
    occupancy: OccupancySource,
    blocks: BlockCalendar,
    ledger: BlockLedger,
    day: str,
    pairs: dict[int, str],
) -> dict[str, Callable[[Any, Any], StepOutcome]]:
    def _sync_blocks_day(provider: Any, step: Any) -> StepOutcome:
        try:
            plan = plan_day(occupancy, blocks, ledger, day, pairs)
        except (TheveaError, DoctolibError) as exc:
            return StepOutcome(ok=False, note=f"read failed: {exc}")
        for event_id in plan.gone:
            ledger.forget(event_id)  # removed in doctolib by someone: nothing left to own
        made = removed = 0
        try:
            for block in plan.create:
                ledger.record(day, blocks.create_block(block, TITLE, NOTES), block)
                made += 1
            for event_id, block in plan.delete:
                blocks.delete_block(event_id, block)
                ledger.forget(event_id)
                removed += 1
        except DoctolibError as exc:
            return StepOutcome(
                ok=False, note=f"doctolib write failed after {made} created, {removed} removed: {exc}"
            )
        return StepOutcome(ok=True, note=f"{day}: {made} blocks created, {removed} removed (claim)")

    return {"sync_blocks_day": _sync_blocks_day}
