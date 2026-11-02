"""Lets a definition's own tests import its code the way its functions do: `betriebskosten.…`,
`shared.…`, and `sdk` (the sandbox's helper library). In the platform nothing imports it; only
these tests run it in-process, and the sandbox runs it everywhere else."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
_SDK = ROOT.parent / "app" / "functions" / "guest"

for path in [_SDK, *ROOT.glob("*"), *(p.parent for p in ROOT.glob("*/*/definition.yaml"))]:
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))


import pytest  # noqa: E402

from app.kernel import modules  # noqa: E402


@pytest.fixture(autouse=True)
def _shipped_definitions(monkeypatch):
    """A template's tests that go through the platform see the shipped definitions."""
    monkeypatch.setattr(modules, "ROOT", ROOT)
