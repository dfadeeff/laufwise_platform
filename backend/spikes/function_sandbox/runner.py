"""Spike (ADR-0039 D5, step 1): run a function in CPython compiled to WASI, under wasmtime.

Nothing here is platform code yet. It answers three questions:
1. Does the NK-01/NK-02 calculator run in WASI Python and give the same result as on the host?
2. What does a call cost (start-up, run time, fuel)?
3. Do the limits hold: no host file, no network, no process, bounded time, memory and output?

Host requirements (not project dependencies until the spike says yes):
- `wasmtime` (PyPI) in the interpreter that runs this file;
- an extracted CPython WASI build: a directory with `python.wasm` and `lib/python3.14/`
  (https://github.com/brettcannon/cpython-wasi-build, v3.14.7).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
GUEST = Path(__file__).with_name("guest")

# What the bundle carries from the backend: the module's code for the settlement, and the
# kernel's stdlib-only helper as the platform library. Never workflow.py (it imports app.kernel).
_SOURCES = {
    "app/modules/immobilien/letters.py": "app/modules/immobilien/letters.py",
    "app/modules/immobilien/betriebskosten/model.py": "app/modules/immobilien/betriebskosten/model.py",
    "app/modules/immobilien/betriebskosten/calculate.py": "app/modules/immobilien/betriebskosten/calculate.py",
    "app/modules/immobilien/betriebskosten/verify.py": "app/modules/immobilien/betriebskosten/verify.py",
    "app/kernel/schema.py": "sdk.py",
}
_PACKAGES = ("app", "app/modules", "app/modules/immobilien", "app/modules/immobilien/betriebskosten")

MEMORY_BYTES = 256 * 1024 * 1024
TIMEOUT_S = 10.0
FUEL = 50_000_000_000
MAX_OUTPUT = 4 * 1024 * 1024


def build_bundle(into: Path) -> Path:
    """The read-only directory a function sees as /bundle."""
    shutil.copytree(GUEST, into, ignore=shutil.ignore_patterns("__pycache__"))
    for package in _PACKAGES:
        (into / package).mkdir(parents=True, exist_ok=True)
        (into / package / "__init__.py").write_text("", encoding="utf-8")
    for source, target in _SOURCES.items():
        (into / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(BACKEND / source, into / target)
    return into


@dataclass
class Outcome:
    ok: bool
    output: dict | None
    error: str
    seconds: float
    fuel_used: int | None = None


def _decode(raw: bytes, seconds: float, fuel: int | None) -> Outcome:
    if len(raw) > MAX_OUTPUT:
        return Outcome(False, None, f"output too large ({len(raw)} bytes)", seconds, fuel)
    try:
        out = json.loads(raw or b"null")
    except json.JSONDecodeError:
        return Outcome(False, None, f"not JSON: {raw[:200]!r}", seconds, fuel)
    if not isinstance(out, dict) or not out.get("ok"):
        return Outcome(False, out, (out or {}).get("error", "no output"), seconds, fuel)
    return Outcome(True, out["result"], "", seconds, fuel)


def run_on_host(bundle: Path, payload: dict, python: str = "/opt/homebrew/bin/python3.14") -> Outcome:
    """The same bundle in an isolated host CPython with the standard library only: the control."""
    start = time.perf_counter()
    done = subprocess.run(
        [python, "-I", "-S", "-B", str(bundle / "entry.py")],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=TIMEOUT_S,
        cwd=bundle,
    )
    return _decode(done.stdout, time.perf_counter() - start, None)


class Sandbox:
    """One compiled python.wasm, many calls. Each call gets a fresh store: no state survives it."""

    def __init__(self, python_dir: Path) -> None:
        from wasmtime import Config, Engine, Linker, Module

        config = Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        config.cache = True
        self.engine = Engine(config)
        start = time.perf_counter()
        self.module = Module.from_file(self.engine, str(python_dir / "python.wasm"))
        self.compile_seconds = time.perf_counter() - start
        self.linker = Linker(self.engine)
        self.linker.define_wasi()
        self.python_dir = python_dir

    def run(self, bundle: Path, payload: dict) -> Outcome:
        from wasmtime import ExitTrap, Store, Trap, WasiConfig, WasmtimeError

        work = Path(tempfile.mkdtemp(prefix="fn-"))
        (work / "in.json").write_text(json.dumps(payload), encoding="utf-8")
        store = Store(self.engine)
        store.set_limits(memory_size=MEMORY_BYTES)
        store.set_fuel(FUEL)
        store.set_epoch_deadline(1)
        wasi = WasiConfig()
        wasi.argv = ["python", "-I", "-S", "-B", "/bundle/entry.py"]
        # Read-only, and nothing else: the interpreter's own files at /, the function at /bundle.
        wasi.preopen_dir(str(self.python_dir), "/", fs_mutable=False)
        wasi.preopen_dir(str(bundle), "/bundle", fs_mutable=False)
        wasi.stdin_file = str(work / "in.json")
        wasi.stdout_file = str(work / "out.json")
        wasi.stderr_file = str(work / "err.txt")
        store.set_wasi(wasi)

        timer = threading.Timer(TIMEOUT_S, self.engine.increment_epoch)
        start = time.perf_counter()
        timer.start()
        try:
            instance = self.linker.instantiate(store, self.module)
            instance.exports(store)["_start"](store)
            exit_code = 0
        except ExitTrap as exc:
            exit_code = exc.code
        except (Trap, WasmtimeError) as exc:
            seconds = time.perf_counter() - start
            return Outcome(False, None, f"trap: {str(exc).splitlines()[0]}", seconds, FUEL - store.get_fuel())
        finally:
            timer.cancel()
        seconds = time.perf_counter() - start
        fuel = FUEL - store.get_fuel()
        out_path = work / "out.json"
        size = out_path.stat().st_size if out_path.exists() else 0
        if size > MAX_OUTPUT:
            return Outcome(False, None, f"output too large ({size} bytes)", seconds, fuel)
        outcome = _decode(out_path.read_bytes() if size else b"", seconds, fuel)
        if exit_code and outcome.ok:
            return Outcome(False, None, f"exit code {exit_code}", seconds, fuel)
        if not outcome.ok and not outcome.output:
            err = (work / "err.txt").read_text(encoding="utf-8", errors="replace")[-300:]
            outcome.error = f"{outcome.error}; exit {exit_code}; stderr: {err.strip()}"
        return outcome
