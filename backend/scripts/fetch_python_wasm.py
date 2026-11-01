"""Fetch the pinned CPython for WASI that functions run in (ADR-0039 D5).

    python scripts/fetch_python_wasm.py [target dir, default ./vendor/python-wasm]

Downloads the release archive, checks it against its pinned SHA-256, and unpacks only
`python.wasm` and `lib/`: nothing else is mounted into the sandbox, so nothing else is kept. Run
by the Docker build and CI; once by hand in development. Idempotent: a target that already holds
the pinned interpreter is left as it is.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.14.7/python-3.14.7-wasi_sdk-24.zip"
ARCHIVE_SHA256 = "2e064d3fb8172471d39d741348efa722349c40b96301f69968dff714999c584b"
PYTHON_WASM_SHA256 = "d24bd98d3071af6b17d51d53a08700b9acef59172a0afcb6adb733645c2a1715"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(target: Path) -> None:
    wasm = target / "python.wasm"
    if wasm.is_file() and _sha256(wasm.read_bytes()) == PYTHON_WASM_SHA256 and (target / "lib").is_dir():
        print(f"{target}: already the pinned interpreter")
        return
    with urllib.request.urlopen(URL, timeout=120) as response:  # noqa: S310 - a pinned https URL
        archive = response.read()
    if _sha256(archive) != ARCHIVE_SHA256:
        sys.exit(f"the archive from {URL} does not match its pinned checksum; refusing it")
    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in zf.namelist():
            path = Path(member)
            if path.is_absolute() or ".." in path.parts:
                sys.exit(f"refusing an archive entry outside the target: {member}")
            if member == "python.wasm" or member.startswith("lib/"):
                zf.extract(member, staging)
    if _sha256((staging / "python.wasm").read_bytes()) != PYTHON_WASM_SHA256:
        sys.exit("python.wasm in the archive does not match its pinned checksum; refusing it")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    print(f"{target}: CPython 3.14.7 for WASI, checked")


if __name__ == "__main__":
    fetch(Path(sys.argv[1] if len(sys.argv) > 1 else "vendor/python-wasm").resolve())
