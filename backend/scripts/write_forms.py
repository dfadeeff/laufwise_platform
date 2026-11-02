"""Write each definition's forms as JSON Schema (ADR-0039 D2).

    python scripts/write_forms.py           # write definitions/*/*/forms/*.json
    python scripts/write_forms.py --check   # fail if any is out of date (run by the tests)

A definition written in Python describes its forms with dataclasses in `<key>/forms.py`; this
turns them into the JSON the platform reads. The platform never imports the dataclasses.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.kernel.schema import object_schema  # noqa: E402

DEFINITIONS = BACKEND / "definitions"


def forms_of(definition: Path) -> dict[str, str]:
    """The three forms of one definition, as the text of their JSON files."""
    key = definition.name
    for path in (definition, definition.parent):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.import_module(f"{key}.forms").FORMS
    labels, choices = spec["labels"], spec["choices"]
    schemas = {
        "input": object_schema(spec["input"], labels, choices),
        "settings": object_schema(spec["settings"], labels, choices),
        "result": object_schema(spec["result"], labels, choices, omit=spec.get("result_omit", ())),
    }
    return {name: json.dumps(schema, ensure_ascii=False, indent=2) + "\n" for name, schema in schemas.items()}


def definitions(root: Path = DEFINITIONS) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/*/definition.yaml") if (p.parent / p.parent.name / "forms.py").exists())


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for definition in definitions():
        for name, text in forms_of(definition).items():
            path = definition / "forms" / f"{name}.json"
            if path.exists() and path.read_text(encoding="utf-8") == text:
                continue
            if check:
                stale.append(str(path.relative_to(BACKEND)))
            else:
                path.parent.mkdir(exist_ok=True)
                path.write_text(text, encoding="utf-8")
                print(f"wrote {path.relative_to(BACKEND)}")
    if stale:
        print("out of date (run scripts/write_forms.py):\n  " + "\n  ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
