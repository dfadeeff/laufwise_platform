"""The function sandbox (ADR-0039 D5): the NK settlements run in it with the expert's figures, and
nothing a function does reaches the host.

Needs `wasmtime` and the interpreter (`scripts/fetch_python_wasm.py`). Without them the tests skip
locally; CI sets LAUFWISE_REQUIRE_SANDBOX=1 so a missing sandbox fails instead of hiding.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.functions.bundle import build_bundle
from app.functions.sandbox import FunctionFailed, Limits, Sandbox, SandboxUnavailable, sandbox

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
MODULE = BACKEND / "app" / "modules" / "immobilien"

try:
    SANDBOX: Sandbox | None = sandbox()
    _missing = ""
except (ImportError, SandboxUnavailable) as exc:  # wasmtime not installed, or no interpreter
    SANDBOX, _missing = None, str(exc)

if SANDBOX is None and os.environ.get("LAUFWISE_REQUIRE_SANDBOX"):
    raise RuntimeError(f"the function sandbox is required here: {_missing}")
needs_sandbox = pytest.mark.skipif(SANDBOX is None, reason=f"no function sandbox: {_missing}")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    """The settlement code as it is in the module, plus two test functions."""
    return build_bundle(
        {
            "functions/betriebskosten_settle.py": FIXTURES / "functions" / "betriebskosten_settle.py",
            "functions/probe.py": FIXTURES / "functions" / "probe.py",
            "app/modules/immobilien/letters.py": MODULE / "letters.py",
            **{
                f"app/modules/immobilien/betriebskosten/{name}.py": MODULE / "betriebskosten" / f"{name}.py"
                for name in ("model", "calculate", "verify")
            },
        },
        tmp_path_factory.mktemp("bundle") / "b",
    )


def _json(value):
    return json.loads(json.dumps(value, default=str))


def _settle(bundle: Path, name: str, **kw):
    fixtures = FIXTURES / "betriebskosten"
    return SANDBOX.run(
        bundle,
        "betriebskosten_settle",
        _json(yaml.safe_load((fixtures / f"{name}.yaml").read_text(encoding="utf-8"))),
        {"basis": "unit_share"},
        _json(yaml.safe_load((MODULE / "betriebskosten" / "catalogue.yaml").read_text(encoding="utf-8"))),
        **kw,
    )


def _probe(bundle: Path, attempt: str, limits: Limits = Limits()):
    return SANDBOX.run(bundle, "probe", {"attempt": attempt}, limits=limits)


@needs_sandbox
@pytest.mark.parametrize("name", ["nk01", "nk02"])
def test_a_settlement_in_the_sandbox_is_the_one_in_process_and_has_the_experts_figures(bundle, name) -> None:
    from app.kernel.schema import plain
    from app.modules.immobilien.betriebskosten.calculate import load_catalogue, settle
    from app.modules.immobilien.betriebskosten.model import Policy, parse_case
    from app.modules.immobilien.betriebskosten.verify import verify

    out = _settle(bundle, name)

    catalogue = load_catalogue()
    case = parse_case(yaml.safe_load((FIXTURES / "betriebskosten" / f"{name}.yaml").read_text(encoding="utf-8")))
    settlement = settle(case, catalogue, Policy(basis="unit_share"))
    assert out == _json({"settlement": plain(settlement), "problems": verify(case, settlement, catalogue)})

    expected = yaml.safe_load((FIXTURES / "betriebskosten" / f"{name}.expected.yaml").read_text(encoding="utf-8"))
    balances = {s["party"]: s["balance"] for s in out["settlement"]["statements"]}
    for party, want in expected["statements"].items():
        if "balance" in want:
            assert Decimal(balances[party]) == Decimal(want["balance"]["value"]), want["balance"]["ref"]
    assert out["problems"] == []


@needs_sandbox
def test_a_function_sees_only_its_bundle_and_the_interpreter_and_writes_nothing(bundle) -> None:
    read = _probe(bundle, "read_host_file")
    assert read["escaped"] is False
    assert set(read["root"]) <= {"lib", "python.wasm", "bundle"}
    assert _probe(bundle, "write_file") == {"escaped": False}


@needs_sandbox
def test_a_function_has_no_network_and_no_processes(bundle) -> None:
    assert _probe(bundle, "network")["escaped"] is False
    assert _probe(bundle, "subprocess")["escaped"] is False


@needs_sandbox
def test_an_endless_loop_stops_at_the_time_limit_and_at_the_instruction_limit(bundle) -> None:
    start = time.perf_counter()
    with pytest.raises(FunctionFailed) as timed:
        _probe(bundle, "loop", Limits(seconds=1, fuel=10**13))
    assert timed.value.kind == "time" and time.perf_counter() - start < 3

    with pytest.raises(FunctionFailed) as counted:
        _probe(bundle, "loop", Limits(seconds=30, fuel=6_000_000_000))
    assert counted.value.kind == "fuel"


@needs_sandbox
def test_memory_and_output_are_bounded_and_an_error_is_reported_as_one(bundle) -> None:
    with pytest.raises(FunctionFailed) as memory:
        _probe(bundle, "memory", Limits(memory_bytes=128 * 1024 * 1024))
    assert memory.value.kind == "memory"

    with pytest.raises(FunctionFailed) as output:
        _probe(bundle, "huge_output")
    assert output.value.kind == "output"

    with pytest.raises(FunctionFailed) as error:
        _probe(bundle, "no such attempt")
    assert error.value.kind == "error" and "ValueError" in error.value.message


@needs_sandbox
def test_calls_at_the_same_time_keep_their_own_deadlines(bundle) -> None:
    """The time limit is per call: one function running out of time stops no other."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        looping = pool.submit(_probe, bundle, "loop", Limits(seconds=1, fuel=10**13))
        settling = pool.submit(_settle, bundle, "nk02")
        assert settling.result()["problems"] == []
        with pytest.raises(FunctionFailed) as timed:
            looping.result()
    assert timed.value.kind == "time"


@needs_sandbox
def test_an_interpreter_that_is_not_the_pinned_one_is_refused(tmp_path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "python.wasm").write_bytes(b"\0asm not the pinned one")
    with pytest.raises(SandboxUnavailable, match="not the pinned"):
        Sandbox(tmp_path)


def test_the_fetch_script_pins_the_interpreter_the_sandbox_checks() -> None:
    from app.functions import sandbox as module

    script = (BACKEND / "scripts" / "fetch_python_wasm.py").read_text(encoding="utf-8")

    assert f'PYTHON_WASM_SHA256 = "{module.PYTHON_WASM_SHA256}"' in script


@pytest.mark.parametrize("path", ["../escape.py", "/abs.py", "entry.py", "sdk.py", "lib.so", "data.json"])
def test_a_bundle_holds_python_source_inside_itself_only(tmp_path, path) -> None:
    with pytest.raises(ValueError):
        build_bundle({path: "x = 1\n"}, tmp_path / "b")
