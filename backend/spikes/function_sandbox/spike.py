"""Run the sandbox spike and print what it found.

    # control only: the bundle in an isolated host CPython, standard library only
    ./.venv/bin/python -m spikes.function_sandbox.spike --host-only
    # the sandbox (needs `wasmtime` and an extracted CPython WASI build)
    <python with wasmtime> -m spikes.function_sandbox.spike --python-wasm <dir>

Pass means: the NK-01 and NK-02 settlements from the sandbox equal the in-process ones exactly,
the expert's key figures come out, and every probe fails to leave the sandbox.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

from spikes.function_sandbox.runner import BACKEND, Sandbox, build_bundle, run_on_host

FIXTURES = BACKEND / "tests" / "fixtures" / "betriebskosten"
CATALOGUE = BACKEND / "app" / "modules" / "immobilien" / "betriebskosten" / "catalogue.yaml"
SETTINGS = {"basis": "unit_share"}

# The expert's figures (fixtures/betriebskosten/nk0N.expected.yaml), the ones the review turned on.
KEY_FIGURES = {
    "nk01": {"Familie Brettschneider": "171.40"},
    "nk02": {"Herr Özdemir": "58.47"},
}

PROBES = ("read_host_file", "write_file", "network", "subprocess", "loop", "memory", "huge_output", "clock")


def _json(value):
    return json.loads(json.dumps(value, default=str))


def _payload(name: str) -> dict:
    return {
        "function": "betriebskosten_settle",
        "input": _json(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))),
        "settings": SETTINGS,
        "rules": _json(yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))),
    }


def _in_process(name: str) -> dict:
    """The reference: the same code, called directly in the platform's interpreter."""
    from app.kernel.schema import plain
    from app.modules.immobilien.betriebskosten.calculate import load_catalogue, settle
    from app.modules.immobilien.betriebskosten.model import Policy, parse_case
    from app.modules.immobilien.betriebskosten.verify import verify

    catalogue = load_catalogue()
    case = parse_case(_payload(name)["input"])
    settlement = settle(case, catalogue, Policy(basis="unit_share"))
    return _json({"settlement": plain(settlement), "problems": verify(case, settlement, catalogue)})


def main() -> int:
    args = argparse.ArgumentParser()
    args.add_argument("--host-only", action="store_true")
    args.add_argument("--python-wasm", type=Path)
    opts = args.parse_args()

    bundle = build_bundle(Path(tempfile.mkdtemp(prefix="bundle-")) / "bundle")
    if opts.host_only:
        call, where = (lambda payload: run_on_host(bundle, payload)), "host (isolated, stdlib only)"
    else:
        sandbox = Sandbox(opts.python_wasm)
        print(f"compile python.wasm: {sandbox.compile_seconds:.2f}s (cached after the first run)")
        call, where = (lambda payload: sandbox.run(bundle, payload)), "wasmtime sandbox"
    print(f"running in: {where}\n")

    failures = []
    for name in ("nk01", "nk02"):
        first, second = call(_payload(name)), call(_payload(name))
        same = first.ok and first.output == _in_process(name)
        deterministic = first.ok and first.output == second.output
        statements = {s["party"]: s["balance"] for s in (first.output or {}).get("settlement", {}).get("statements", [])}
        figures = all(statements.get(p) == v for p, v in KEY_FIGURES[name].items())
        print(
            f"{name}: ok={first.ok} equals_in_process={same} deterministic={deterministic} "
            f"expert_figures={figures} {first.seconds:.2f}s fuel={first.fuel_used}"
            + (f" error={first.error}" if not first.ok else "")
        )
        if not (same and deterministic and figures):
            failures.append(name)

    print()
    for attempt in PROBES:
        if opts.host_only and attempt in ("loop", "memory", "network", "subprocess", "read_host_file", "write_file"):
            print(f"probe {attempt}: skipped on the host (no sandbox to test)")
            continue
        outcome = call({"function": "probe", "input": {"attempt": attempt}})
        escaped = bool(outcome.ok and (outcome.output or {}).get("escaped"))
        if attempt == "clock":
            held = True  # reported, not a failure: see probe.py
        elif attempt in ("loop", "memory", "huge_output"):
            held = not outcome.ok
        else:
            held = not escaped
        detail = outcome.error if not outcome.ok else json.dumps(outcome.output)[:120]
        print(f"probe {attempt}: {'HELD' if held else 'ESCAPED'} {outcome.seconds:.2f}s {detail}")
        if not held:
            failures.append(f"probe {attempt}")

    print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
