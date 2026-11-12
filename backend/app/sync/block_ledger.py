"""The block mirror's ledger, backed by the database (ADR-0044).

The governed contract runs in a worker thread (`Runtime.execute_recorded`) and its providers and
tools are synchronous, while the database is async. So each call is handed to the event loop the
run was started from and waited on — the same way the run's own journal is written. Every write
commits on its own: a block recorded the moment doctolib returned its id stays recorded even if
the run is cut off right after.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.connectors.base import Block
from app.db import repo
from app.db.session import get_sessionmaker


class DbBlockLedger:
    def __init__(
        self, tenant_id: uuid.UUID, connection_id: uuid.UUID, loop: asyncio.AbstractEventLoop
    ) -> None:
        self._tenant, self._connection, self._loop = tenant_id, connection_id, loop

    def _run(self, work: Callable[[Any], Awaitable[Any]]) -> Any:
        async def go() -> Any:
            async with get_sessionmaker()() as session:
                result = await work(session)
                await session.commit()
                return result

        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout=30)

    def owned(self, day: str) -> list[tuple[str, Block]]:
        rows = self._run(lambda s: repo.owned_doctolib_blocks(s, self._tenant, self._connection, day))
        return [(r.event_id, Block(r.agenda_id, _iso(r.starts_at), _iso(r.ends_at))) for r in rows]

    def record(self, day: str, event_id: str, block: Block) -> None:
        self._run(lambda s: repo.record_doctolib_block(
            s, tenant_id=self._tenant, connection_id=self._connection, day=day,
            event_id=event_id, agenda_id=block.agenda,
            starts_at=_at(block.start), ends_at=_at(block.end),
        ))

    def forget(self, event_id: str) -> None:
        self._run(lambda s: repo.forget_doctolib_block(s, self._tenant, self._connection, event_id))


def _at(instant: str) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00"))


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
