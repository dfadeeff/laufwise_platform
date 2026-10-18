"""How many calls this process is carrying right now — so a deploy can wait for them.

A call is a websocket held open for minutes. Railway stops the old container once the new one is
up, and uvicorn's shutdown closes every websocket with 1012 — the caller is cut off mid-sentence.
`app.serve` asks this count before it lets the process stop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_live = 0


def count() -> int:
    return _live


@asynccontextmanager
async def track() -> AsyncIterator[None]:
    """Around one call, from its first frame to its summary email."""
    global _live
    _live += 1
    try:
        yield
    finally:
        _live -= 1
