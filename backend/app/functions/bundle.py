"""The read-only directory a function sees as `/bundle` (ADR-0039 D5).

A bundle is the platform's entry point and helper library, plus the agent's own source files at
the paths the agent's code imports them by. Every directory becomes a package. The bundle holds
Python source only: a function cannot bring a binary.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Mapping

GUEST = Path(__file__).with_name("guest")
_PLATFORM = ("entry.py", "sdk.py")


def build_bundle(files: Mapping[str, Path | str], into: Path) -> Path:
    """Write a bundle to `into` (which must not exist yet) and return it. `files` maps a path in
    the bundle (e.g. "functions/settle.py") to its source: a file on disk, or the text itself."""
    into.mkdir(parents=True)
    for name in _PLATFORM:
        shutil.copy(GUEST / name, into / name)
    for guest_path, source in files.items():
        target = into / _checked(guest_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, Path):
            shutil.copy(source, target)
        else:
            target.write_text(source, encoding="utf-8")
    for directory in [d for d in into.rglob("*") if d.is_dir()]:
        init = directory / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    return into


def _checked(guest_path: str) -> PurePosixPath:
    path = PurePosixPath(guest_path)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ValueError(f"not a source path inside the bundle: {guest_path!r}")
    if len(path.parts) == 1 and path.name in _PLATFORM:
        raise ValueError(f"{guest_path!r} is the platform's own file")
    return path
