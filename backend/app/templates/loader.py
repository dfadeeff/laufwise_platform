"""Load a template contract from a YAML file (Stage 1 filesystem source).

In Stage 2 templates move to the `Template` table (contract JSONB); this loader is the
filesystem stand-in until then. `TemplateContract.model_validate` does the schema validation.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.templates.contract import TemplateContract


def load_template(path: str | Path) -> TemplateContract:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return TemplateContract.model_validate(raw)