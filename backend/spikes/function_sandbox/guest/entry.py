"""Runs inside the sandbox (ADR-0039 D5): JSON in on stdin, JSON out on stdout, nothing else.

The payload names a function in `functions/` and carries everything it may use: the input, the
settings and the rules. The function never reads a file, the network or the clock; whatever it
needs comes in here.
"""

import json
import os
import sys
import traceback
from importlib import import_module

# `-I` keeps the script's directory off sys.path; the bundle is the one place code comes from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    payload = json.loads(sys.stdin.read())
    try:
        function = import_module(f"functions.{payload['function']}")
        out = {"ok": True, "result": function.run(payload["input"], payload.get("settings", {}), payload.get("rules", {}))}
    except Exception as exc:  # the host reports it; the run fails this step
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=5)}
    sys.stdout.write(json.dumps(out))


main()
