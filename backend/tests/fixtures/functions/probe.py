"""Functions that try to leave the sandbox. Each returns what it managed; the spike expects none
to manage anything except reading the clock (WASI grants a clock; ADR-0039 D5 keeps dates out of
functions by passing them in, and the determinism check below holds that)."""

import os
import time


def run(input: dict, settings: dict, rules: dict) -> dict:
    attempt = input["attempt"]
    if attempt == "read_host_file":
        for path in ("/etc/passwd", "/Users", "../../etc/passwd", "/bundle/../../etc/passwd"):
            try:
                with open(path) as f:
                    return {"escaped": True, "path": path, "head": f.read(40)}
            except OSError:
                pass
        return {"escaped": False, "root": sorted(os.listdir("/"))}
    if attempt == "write_file":
        for path in ("/bundle/planted.py", "/planted.txt", "/tmp/planted.txt"):
            try:
                with open(path, "w") as f:
                    f.write("x")
                return {"escaped": True, "path": path}
            except OSError:
                pass
        return {"escaped": False}
    if attempt == "network":
        try:
            import socket

            s = socket.create_connection(("1.1.1.1", 80), timeout=2)
            s.close()
            return {"escaped": True}
        except Exception as exc:
            return {"escaped": False, "error": type(exc).__name__}
    if attempt == "subprocess":
        try:
            import subprocess

            subprocess.run(["ls"], check=True)
            return {"escaped": True}
        except Exception as exc:
            return {"escaped": False, "error": type(exc).__name__}
    if attempt == "loop":
        while True:
            pass
    if attempt == "memory":
        chunks = []
        while True:
            chunks.append(bytearray(16 * 1024 * 1024))
    if attempt == "huge_output":
        return {"blob": "x" * (20 * 1024 * 1024)}
    if attempt == "clock":
        return {"time": time.time()}
    raise ValueError(attempt)
