"""A deploy must not cut a call off: SIGTERM waits for live calls (app.serve).

Run against a real server process, because the failure is in uvicorn's shutdown itself: it closes
every websocket with 1012, and a phone call is a websocket.
"""

from __future__ import annotations

import asyncio
import signal
import socket
import subprocess
import sys
import textwrap
import time

import pytest
import websockets

_SERVER = textwrap.dedent(
    """
    import sys, uvicorn
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from app.serve import DrainingServer
    from app.workloads.conversational import live_calls

    app = FastAPI()

    @app.websocket("/call")
    async def call(ws: WebSocket):
        await ws.accept()
        async with live_calls.track():
            try:
                while True:
                    await ws.send_text(await ws.receive_text())
            except WebSocketDisconnect:
                pass

    DrainingServer(
        uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning"),
        drain_limit=float(sys.argv[2]),
    ).run()
    """
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(drain_limit: float = 30) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", _SERVER, str(port), str(drain_limit)])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc, port
        except OSError:
            time.sleep(0.1)
    proc.kill()
    pytest.fail("test server did not start")


def _exited_within(proc: subprocess.Popen, seconds: float) -> bool:
    try:
        proc.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def test_a_deploy_waits_for_the_live_call_then_stops() -> None:
    proc, port = _start()

    async def call() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{port}/call") as ws:
            await ws.send("hallo")
            assert await ws.recv() == "hallo"
            proc.send_signal(signal.SIGTERM)
            await asyncio.sleep(1.5)
            # Still talking after the deploy's SIGTERM: the call was not cut off.
            await ws.send("noch da?")
            assert await ws.recv() == "noch da?"
            assert proc.poll() is None

    try:
        asyncio.run(call())
        # The call ended, so the old container goes.
        assert _exited_within(proc, 10)
    finally:
        proc.kill()


def test_without_calls_it_stops_at_once() -> None:
    proc, _port = _start()
    try:
        proc.send_signal(signal.SIGTERM)
        assert _exited_within(proc, 10)
    finally:
        proc.kill()


def test_a_second_signal_stops_it_even_with_a_call() -> None:
    proc, port = _start()

    async def call() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{port}/call") as ws:
            await ws.send("hallo")
            await ws.recv()
            proc.send_signal(signal.SIGTERM)
            await asyncio.sleep(1)
            proc.send_signal(signal.SIGTERM)
            assert _exited_within(proc, 10)

    try:
        asyncio.run(call())
    except websockets.ConnectionClosed:
        pass
    finally:
        proc.kill()


def test_the_wait_is_bounded() -> None:
    """A call that never ends cannot hold a deploy forever."""
    proc, port = _start(drain_limit=2)

    async def call() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{port}/call") as ws:
            await ws.send("hallo")
            await ws.recv()
            proc.send_signal(signal.SIGTERM)
            assert _exited_within(proc, 10)

    try:
        asyncio.run(call())
    except websockets.ConnectionClosed:
        pass
    finally:
        proc.kill()
