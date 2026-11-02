"""Reading modules and their workflow definitions from files (ADR-0039 D2).

    definitions/<module>/module.yaml
    definitions/<module>/shared/*.py              in every bundle of the module, as `shared`
    definitions/<module>/<key>/definition.yaml
    definitions/<module>/<key>/forms/*.json
    definitions/<module>/<key>/rules/...
    definitions/<module>/<key>/guide.md
    definitions/<module>/<key>/**/*.py             the bundle; never tests/, forms.py or host.py

Everything is checked here, at boot: a definition that cannot be run stops the process rather
than failing one case later. Nothing is imported: the Python is copied into a bundle the sandbox
runs.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app.functions.bundle import build_bundle
from app.kernel.workflows import Workflow

DEFINITIONS = Path(__file__).resolve().parents[2] / "definitions"
_FORMS = ("input", "settings", "result")
_REQUIRED_FUNCTIONS = ("compute", "check")
_KNOWN_FUNCTIONS = {"compute", "check", "letters"}
_HOST_ONLY = {"forms.py", "host.py"}


class DefinitionError(Exception):
    """A module or definition on disk the kernel cannot accept. Raised at boot."""


def _yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DefinitionError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DefinitionError(f"{path}: not a mapping")
    return data


def _as_json(value: Any) -> Any:
    """YAML data in the form a function receives it: dates as ISO text."""
    return json.loads(json.dumps(value, default=str))


def module_manifests(root: Path = DEFINITIONS) -> list[tuple[Path, dict]]:
    """Each module's directory and manifest, in the order a new workspace is offered them."""
    if not root.is_dir():
        return []
    found = [(p.parent, _yaml(p)) for p in sorted(root.glob("*/module.yaml"))]
    return sorted(found, key=lambda m: (m[1].get("order", 0), m[0].name))


def load_definitions(module_dir: Path, bundles: Path) -> tuple[Workflow, ...]:
    return tuple(
        load_definition(p.parent, module_dir, bundles)
        for p in sorted(module_dir.glob("*/definition.yaml"))
    )


def load_definition(directory: Path, module_dir: Path, bundles: Path) -> Workflow:
    spec = _yaml(directory / "definition.yaml")
    where = f"definition {directory.relative_to(module_dir.parent)}"
    key = spec.get("key")
    if key != directory.name:
        raise DefinitionError(f"{where} declares key {key!r}, not its directory's name")
    functions = tuple(spec.get("functions", ()))
    if missing := [f for f in _REQUIRED_FUNCTIONS if f not in functions]:
        raise DefinitionError(f"{where} has no {', '.join(missing)} function")
    if unknown := set(functions) - _KNOWN_FUNCTIONS:
        raise DefinitionError(f"{where} declares unknown functions: {sorted(unknown)}")
    for name in functions:
        if not (directory / "functions" / f"{name}.py").is_file():
            raise DefinitionError(f"{where}: functions/{name}.py is missing")

    forms = {}
    for name in _FORMS:
        path = directory / "forms" / f"{name}.json"
        try:
            forms[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DefinitionError(f"{where}: forms/{name}.json: {exc}") from exc
        if forms[name].get("type") != "object":
            raise DefinitionError(f"{where}: forms/{name}.json is not an object form")

    rules_spec = spec.get("rules") or {}
    rules = _as_json(_yaml(directory / rules_spec.get("file", "rules/rules.yaml")))
    if "version" not in rules:
        raise DefinitionError(f"{where}: its rules have no version")
    guide = directory / "guide.md"

    sources: dict[str, Path] = {}
    for path in sorted(directory.rglob("*.py")):
        relative = path.relative_to(directory)
        if relative.parts[0] == "tests" or "__pycache__" in relative.parts:
            continue
        if path.name in _HOST_ONLY:
            continue  # read on the host for the forms and the tests; the sandbox never needs it
        sources[relative.as_posix()] = path
    shared = module_dir / "shared"
    for path in sorted(shared.rglob("*.py")) if shared.is_dir() else ():
        if "__pycache__" not in path.parts:
            sources[f"shared/{path.relative_to(shared).as_posix()}"] = path

    return Workflow(
        key=str(key),
        label=str(spec.get("label", key)),
        summary=str(spec.get("summary", "")),
        version=int(spec.get("version", 1)),
        forms=forms,
        rules=rules,
        rules_version=f"{rules_spec.get('name', 'rules')} v{rules['version']}",
        extraction_guide=guide.read_text(encoding="utf-8") if guide.is_file() else "",
        bundle=build_bundle(sources, bundles / f"{module_dir.name}-{key}-v{spec.get('version', 1)}"),
        functions=functions,
    )


def bundles_dir() -> Path:
    """Where this process keeps its bundles: read-only to every function, rebuilt at boot."""
    return Path(tempfile.mkdtemp(prefix="laufwise-bundles-"))
