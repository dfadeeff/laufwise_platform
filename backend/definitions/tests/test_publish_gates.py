"""Every shipped definition passes its own cases, as the publish gate runs them (ADR-0039 step 4).

The boot runs the same gate before it publishes a new version; this is the same proof, in CI,
before the version reaches a deploy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.definitions.gate import run_gate
from app.kernel.definitions import bundles_dir, module_manifests, read_definitions, workflow_from

SHIPPED = [content for directory, _ in module_manifests(Path(__file__).parents[1]) for content in read_definitions(directory)]


@pytest.mark.parametrize("content", SHIPPED, ids=[c["key"] for c in SHIPPED])
def test_the_shipped_definition_passes_every_case_it_names(content) -> None:
    report = run_gate(workflow_from(content, bundles_dir()), content)
    failures = [f"{c['name']}: {f}" for c in report["cases"] for f in c["failures"]]
    assert report["passed"], report.get("refused") or failures
