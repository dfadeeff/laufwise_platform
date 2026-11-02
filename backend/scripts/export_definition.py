"""Export a stored definition version to files (ADR-0039 step 2b).

    DATABASE_URL=… python scripts/export_definition.py <key> [version] [--to DIR]

Writes `DIR/<module>/<key>/` (default DIR: ./exported), readable again by the platform to the same
checksum: how a version edited in the Studio becomes files that are reviewed like code. Without a
version, the newest published one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import repo  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.definitions.service import export_files  # noqa: E402


async def main() -> int:
    args = argparse.ArgumentParser()
    args.add_argument("key")
    args.add_argument("version", nargs="?", type=int)
    args.add_argument("--to", type=Path, default=Path("exported"))
    opts = args.parse_args()
    async with get_sessionmaker()() as session:
        definition = await repo.get_definition(session, opts.key)
        if definition is None:
            print(f"no definition {opts.key!r}")
            return 1
        if opts.version is None:
            found = [v for d, v in await repo.latest_published_versions(session) if d.id == definition.id]
            version = found[0] if found else None
        else:
            version = await repo.get_definition_version(session, definition.id, opts.version)
        if version is None:
            print(f"{opts.key}: no such version")
            return 1
    directory = export_files(version.content, opts.to)
    print(f"{opts.key} v{version.version} ({version.status}) -> {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
