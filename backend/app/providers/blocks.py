"""The doctolib block mirror's state (ADR-0044): does each doctolib agenda block exactly the times
its paired thevea room is taken on this day?

The practice books in two places. A treatment room taken in thevea (MA 2, MA 3) must not stay
bookable online in the doctolib agenda that stands for it (Behandlungsraum 1, 2), and until now the
practice closed those times by hand, one "Abwesenheit" at a time. The mirror does it for them:

- the WANTED blocks are the room's taken times in thevea, joined where they touch, minus every
  time the agenda already has a doctolib appointment (a block over an appointment may suspend it);
- the PRESENT blocks are the ones this mirror created, read back from doctolib one by one — there
  is no endpoint that lists blocks, so the ledger keeps their ids. A block someone else set by hand
  is never seen, so it is never changed or removed.

`plan_day` is the one place the two are compared, and both the binding and the tool use it, so the
check that decides "in sync" is the same function the write is planned from.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from laufwise.state.base import StateUnavailable, StateView

from app.connectors.base import Block, BlockCalendar, BusyRange, OccupancySource
from app.providers.doctolib import DoctolibError
from app.providers.thevea import TheveaError

TITLE = "laufwise · belegt (thevea)"
NOTES = "In thevea belegt. Von laufwise gesetzt und wieder entfernt — bitte nicht bearbeiten."


class BlockLedger(Protocol):
    """The blocks this mirror created and has not removed — the only ones it may ever touch."""

    def owned(self, day: str) -> list[tuple[str, Block]]: ...

    def record(self, day: str, event_id: str, block: Block) -> None: ...

    def forget(self, event_id: str) -> None: ...


@dataclass
class Plan:
    wanted: list[Block] = field(default_factory=list)
    create: list[Block] = field(default_factory=list)
    delete: list[tuple[str, Block]] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # recorded blocks no longer in doctolib

    @property
    def in_sync(self) -> bool:
        return not self.create and not self.delete


def _at(instant: str) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _minute(at: datetime, *, up: bool) -> datetime:
    """Whole minutes: thevea ends an absent day at 23:59:59, doctolib stores minutes."""
    floor = at.replace(second=0, microsecond=0)
    return floor + timedelta(minutes=1) if up and floor != at else floor


def wanted(busy: list[BusyRange], taken: list[Block], pairs: dict[int, str]) -> list[Block]:
    """The blocks each agenda should have: its room's taken times joined, minus its bookings."""
    out: list[Block] = []
    for room, agenda in pairs.items():
        spans = sorted(
            (_minute(_at(b.start), up=False), _minute(_at(b.end), up=True))
            for b in busy if int(b.room) == room
        )
        joined: list[list[datetime]] = []
        for start, end in spans:
            if end <= start:
                continue
            if joined and start <= joined[-1][1]:
                joined[-1][1] = max(joined[-1][1], end)
            else:
                joined.append([start, end])
        holes = sorted((_at(t.start), _at(t.end)) for t in taken if t.agenda == agenda)
        for start, end in joined:
            at = start
            for hole_start, hole_end in holes:
                if hole_end <= at or hole_start >= end:
                    continue
                if hole_start > at:
                    out.append(Block(agenda, _iso(at), _iso(hole_start)))
                at = max(at, hole_end)
            if at < end:
                out.append(Block(agenda, _iso(at), _iso(end)))
    return sorted(out)


def plan_day(
    occupancy: OccupancySource,
    blocks: BlockCalendar,
    ledger: BlockLedger,
    day: str,
    pairs: dict[int, str],
    now: datetime | None = None,
) -> Plan:
    """What to create and delete so the agendas block exactly the wanted times. A time already
    over is left as it is: closing or reopening the past changes nothing anyone can book."""
    now = now or datetime.now(timezone.utc)
    agendas = sorted(set(pairs.values()))
    plan = Plan(wanted=wanted(occupancy.list_busy(day, list(pairs)), blocks.taken(day, agendas), pairs))
    present: list[tuple[str, Block]] = []
    for event_id, _ in ledger.owned(day):
        block = blocks.get_block(event_id)
        if block is None:
            plan.gone.append(event_id)
        else:
            present.append((event_id, block))
    upcoming = [b for b in plan.wanted if _at(b.end) > now]
    missing = Counter(upcoming)
    for event_id, block in present:
        if missing[block] > 0:
            missing[block] -= 1
        elif _at(block.end) > now:
            plan.delete.append((event_id, block))
    plan.create = sorted(missing.elements())
    return plan


class BlocksDayProvider:
    """Binding `agenda_day`: `in_sync` is true when no block needs creating or deleting. Either
    system failing raises `StateUnavailable`, so the day halts rather than being half-written."""

    def __init__(
        self, occupancy: OccupancySource, blocks: BlockCalendar, ledger: BlockLedger,
        day: str, pairs: dict[int, str],
    ) -> None:
        self._args = (occupancy, blocks, ledger, day, pairs)

    def query(self, name: str, params: dict | None = None) -> StateView:
        try:
            plan = plan_day(*self._args)
        except TheveaError as exc:
            raise StateUnavailable(f"practice calendar unavailable: {exc}") from exc
        except DoctolibError as exc:
            raise StateUnavailable(f"doctolib unavailable: {exc}") from exc
        return StateView(value={
            "in_sync": plan.in_sync, "day": self._args[3], "wanted": len(plan.wanted),
            "to_create": len(plan.create), "to_delete": len(plan.delete),
        })
