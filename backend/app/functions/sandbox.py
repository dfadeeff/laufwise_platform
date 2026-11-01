"""Run a function where it cannot harm anything (ADR-0039 D5).

A function is code the builder wrote for one agent: JSON in, JSON out. It runs in CPython compiled
to WASI, under wasmtime, with two read-only directories and nothing else: the interpreter's
standard library at `/`, the function's bundle at `/bundle`. No network, no process, no writable
file. Each call gets a fresh store, so nothing survives from one call to the next, and it is
bounded in time, instructions, memory and output. A function that exceeds a bound fails with the
bound's name; it never hangs a request.

Time is enforced with wasmtime's epoch: one ticker thread advances the engine's epoch every
`_TICK_S`, and each call sets its own deadline in ticks. Concurrent calls share the engine but
not their deadlines.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings

# The interpreter we run, pinned: CPython 3.14.7 for WASI (github.com/brettcannon/cpython-wasi-build,
# v3.14.7). `scripts/fetch_python_wasm.py` checks the download; this checks what is on disk.
PYTHON_WASM_SHA256 = "d24bd98d3071af6b17d51d53a08700b9acef59172a0afcb6adb733645c2a1715"

_TICK_S = 0.05


@dataclass(frozen=True)
class Limits:
    seconds: float = 10.0
    fuel: int = 50_000_000_000  # instructions, roughly; a settlement takes about 4,4 billion
    memory_bytes: int = 256 * 1024 * 1024
    output_bytes: int = 4 * 1024 * 1024


class SandboxUnavailable(RuntimeError):
    """The interpreter is missing or is not the pinned one. Nothing is run."""


class FunctionFailed(Exception):
    """A function did not return a result. `kind` says why: "time", "fuel", "memory", "output",
    "error" (it raised), "crash" (anything else)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class Sandbox:
    """One compiled interpreter, many calls."""

    def __init__(self, python_dir: Path) -> None:
        from wasmtime import Config, Engine, Linker, Module

        wasm = python_dir / "python.wasm"
        if not wasm.is_file() or not (python_dir / "lib").is_dir():
            raise SandboxUnavailable(
                f"no CPython for WASI in {python_dir}; run scripts/fetch_python_wasm.py"
            )
        digest = hashlib.sha256(wasm.read_bytes()).hexdigest()
        if digest != PYTHON_WASM_SHA256:
            raise SandboxUnavailable(f"{wasm} is not the pinned interpreter (sha256 {digest})")
        self.python_dir = python_dir
        config = Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        config.cache = True
        self._engine = Engine(config)
        self._module = Module.from_file(self._engine, str(wasm))
        self._linker = Linker(self._engine)
        self._linker.define_wasi()
        ticker = threading.Thread(target=self._tick, name="sandbox-epoch", daemon=True)
        ticker.start()

    def _tick(self) -> None:
        while True:
            time.sleep(_TICK_S)
            self._engine.increment_epoch()

    def run(
        self,
        bundle: Path,
        function: str,
        input: Any,
        settings: Any = None,
        rules: Any = None,
        limits: Limits = Limits(),
    ) -> Any:
        """Call `function` (a module in the bundle's `functions/`) and return its result."""
        from wasmtime import ExitTrap, Store, Trap, TrapCode, WasiConfig, WasmtimeError

        work = Path(tempfile.mkdtemp(prefix="laufwise-fn-"))
        try:
            payload = {"function": function, "input": input, "settings": settings or {}, "rules": rules or {}}
            (work / "in.json").write_text(json.dumps(payload), encoding="utf-8")
            store = Store(self._engine)
            store.set_limits(memory_size=limits.memory_bytes)
            store.set_fuel(limits.fuel)
            store.set_epoch_deadline(max(1, math.ceil(limits.seconds / _TICK_S)))
            wasi = WasiConfig()
            wasi.argv = ["python", "-I", "-S", "-B", "/bundle/entry.py"]
            wasi.preopen_dir(str(self.python_dir), "/", fs_mutable=False)
            wasi.preopen_dir(str(bundle), "/bundle", fs_mutable=False)
            wasi.stdin_file = str(work / "in.json")
            wasi.stdout_file = str(work / "out.json")
            wasi.stderr_file = str(work / "err.txt")
            store.set_wasi(wasi)
            try:
                instance = self._linker.instantiate(store, self._module)
                instance.exports(store)["_start"](store)
            except ExitTrap as exc:
                if exc.code != 0:
                    raise FunctionFailed("crash", f"exit code {exc.code}: {_tail(work)}") from None
            except Trap as exc:
                if exc.trap_code == TrapCode.OUT_OF_FUEL:
                    raise FunctionFailed("fuel", f"more than {limits.fuel} instructions") from None
                if exc.trap_code == TrapCode.INTERRUPT:
                    raise FunctionFailed("time", f"longer than {limits.seconds} s") from None
                raise FunctionFailed("crash", str(exc).splitlines()[0]) from None
            except WasmtimeError as exc:
                raise FunctionFailed("crash", str(exc).splitlines()[0]) from None
            return _result(work / "out.json", limits)
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _tail(work: Path) -> str:
    err = work / "err.txt"
    return err.read_text(encoding="utf-8", errors="replace")[-300:].strip() if err.exists() else ""


def _result(path: Path, limits: Limits) -> Any:
    size = path.stat().st_size if path.exists() else 0
    if size > limits.output_bytes:
        raise FunctionFailed("output", f"{size} bytes, more than {limits.output_bytes}")
    try:
        out = json.loads(path.read_bytes() if size else b"null")
    except json.JSONDecodeError:
        raise FunctionFailed("crash", "the function's output is not JSON") from None
    if not isinstance(out, dict):
        raise FunctionFailed("crash", "no output")
    if not out.get("ok"):
        error = str(out.get("error", ""))
        raise FunctionFailed("memory" if error.startswith("MemoryError") else "error", error)
    return out["result"]


@lru_cache
def sandbox() -> Sandbox:
    """The process's sandbox, compiled on first use."""
    return Sandbox(Path(settings.python_wasm_dir).resolve())
