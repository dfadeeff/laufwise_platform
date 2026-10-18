"""Start the API so that a deploy never cuts a call off.

`uvicorn app.main:app` stops on SIGTERM by closing every websocket with 1012, and a live call is a
websocket. Railway sends that SIGTERM to the old container as soon as the new one is healthy, so
every merge used to end every call in progress. Here the first SIGTERM waits until the calls this
process carries have ended (at most `MAX_CALL_SECONDS` plus a minute; new calls already go to the
new container), then stops as uvicorn would. A second signal stops at once.

Railway must allow the wait: `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` at least `DRAIN_LIMIT_SECONDS`,
or it kills the process first (DEPLOY.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import FrameType

import uvicorn

from app.workloads.conversational import live_calls
from app.workloads.conversational.surface import MAX_CALL_SECONDS

log = logging.getLogger(__name__)

DRAIN_LIMIT_SECONDS = MAX_CALL_SECONDS + 60


class DrainingServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, *, drain_limit: float = DRAIN_LIMIT_SECONDS) -> None:
        super().__init__(config)
        self._drain_limit = drain_limit
        self._draining = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def serve(self, sockets=None) -> None:
        self._loop = asyncio.get_running_loop()
        await super().serve(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if self._draining or self._loop is None or live_calls.count() == 0:
            super().handle_exit(sig, frame)
            return
        self._draining = True
        log.warning("shutdown requested with %d live call(s): waiting for them", live_calls.count())
        loop = self._loop
        loop.call_soon_threadsafe(lambda: loop.create_task(self._drain(sig, frame)))

    async def _drain(self, sig: int, frame: FrameType | None) -> None:
        deadline = time.monotonic() + self._drain_limit
        while live_calls.count() and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        log.warning("draining finished with %d live call(s) left; stopping", live_calls.count())
        super().handle_exit(sig, frame)


def main() -> None:
    # The same settings as `uvicorn app.main:app --host 0.0.0.0 --port 8080` (proxy handling
    # included: the Twilio webhook rebuilds its public URL from X-Forwarded-Proto itself).
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8080)
    DrainingServer(config).run()


if __name__ == "__main__":
    main()
