"""Host-side helpers for this definition's tests and its forms: reading the rules file. Never
part of what runs in the sandbox, where the rules arrive as data (ADR-0039 D5)."""

from pathlib import Path

import yaml

from betriebskosten.calculate import Catalogue, catalogue_from

CATALOGUE_PATH = Path(__file__).parents[1] / "rules" / "catalogue.yaml"


def load_catalogue(path: Path = CATALOGUE_PATH) -> Catalogue:
    return catalogue_from(yaml.safe_load(path.read_text(encoding="utf-8")))
