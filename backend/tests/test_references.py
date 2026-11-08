"""The law a definition applies, as texts it carries (ADR-0041 step 1).

A module ships its legal sources; a definition names the ones it applies, its version carries
their text, a draft always carries the module's current text, and the builder reads them on every
run without a person attaching them.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from app.builder.service import _first_turn
from app.db.models import BuilderRun
from app.kernel.definitions import (
    MAX_REFERENCE_CHARS,
    DefinitionError,
    check_content,
    read_definition,
    with_module_references,
)
from tests.conftest import FIXTURE_DEFINITIONS

LAW = """---
title: Musterverordnung (MusterV)
cites: [MusterV]
url: https://www.gesetze-im-internet.de/musterv/
stand: Zuletzt geändert 2025
retrieved: 2026-11-08
---
## § 1 Umlage

Kosten werden nach Fläche verteilt.
"""


@pytest.fixture
def module(tmp_path: Path) -> Path:
    """The demo module, with one legal source its definition applies."""
    shutil.copytree(FIXTURE_DEFINITIONS / "demo", tmp_path / "demo")
    (tmp_path / "demo" / "references").mkdir()
    (tmp_path / "demo" / "references" / "musterv.md").write_text(LAW, encoding="utf-8")
    spec = tmp_path / "demo" / "umlage" / "definition.yaml"
    spec.write_text(spec.read_text(encoding="utf-8") + "references: [musterv]\n", encoding="utf-8")
    return tmp_path / "demo"


def test_a_definition_carries_the_text_of_the_law_it_names(module: Path) -> None:
    content = read_definition(module / "umlage", module)
    (law,) = content["references"]
    assert {k: law[k] for k in ("name", "title", "cites", "url", "stand", "retrieved")} == {
        "name": "musterv", "title": "Musterverordnung (MusterV)", "cites": ["MusterV"],
        "url": "https://www.gesetze-im-internet.de/musterv/", "stand": "Zuletzt geändert 2025",
        "retrieved": "2026-11-08",
    }
    assert law["text"].startswith("## § 1 Umlage") and "nach Fläche" in law["text"]


def test_a_definition_naming_a_source_the_module_does_not_ship_is_refused(module: Path) -> None:
    spec = module / "umlage" / "definition.yaml"
    spec.write_text(spec.read_text(encoding="utf-8").replace("[musterv]", "[musterv, bgb]"), encoding="utf-8")
    with pytest.raises(DefinitionError, match="bgb"):
        read_definition(module / "umlage", module)


@pytest.mark.parametrize(
    ("change", "refused"),
    [
        (lambda r: r[0].update(text=""), "has no text"),
        (lambda r: r[0].update(cites=[]), "names how it is cited"),
        (lambda r: r[0].update(text="x" * (MAX_REFERENCE_CHARS + 1)), "characters of law"),
    ],
)
def test_sources_a_definition_cannot_be_built_from_are_refused(module: Path, change, refused) -> None:
    content = read_definition(module / "umlage", module)
    change(content["references"])
    with pytest.raises(DefinitionError, match=refused):
        check_content(content)


def test_a_draft_always_carries_the_modules_text_of_the_law(module: Path) -> None:
    # The law is the platform's (ADR-0038): a draft that changed it gets the shipped text back.
    content = read_definition(module / "umlage", module)
    edited = copy.deepcopy(content)
    edited["references"][0]["text"] = "Kosten trägt immer der Mieter."
    assert with_module_references(edited, module) == content

    (module / "references" / "musterv.md").write_text(LAW.replace("nach Fläche", "nach Köpfen"), encoding="utf-8")
    assert "nach Köpfen" in with_module_references(edited, module)["references"][0]["text"]

    without = {k: v for k, v in content.items() if k != "references"}
    assert with_module_references(without, module) == without


def test_the_builder_reads_the_law_on_every_run_but_not_inside_the_json(module: Path) -> None:
    content = read_definition(module / "umlage", module)
    run = BuilderRun(instruction="Schreibe die Umlage neu.", file_ids=[])
    turn = _first_turn(run, content, [])
    shown_json = turn["text"].split("as JSON:\n", 1)[1]
    assert "nach Fläche" not in shown_json and '"references"' not in shown_json
    (law,) = turn["documents"]
    assert law.name == "Musterverordnung (MusterV) (Stand: Zuletzt geändert 2025)"
    assert law.media_type == "text/markdown" and b"nach Fl\xc3\xa4che" in law.data
    assert "Cite only what they say" in turn["text"]
