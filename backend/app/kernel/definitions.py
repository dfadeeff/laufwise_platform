"""Reading modules and their workflow definitions from files (ADR-0039 D2).

    definitions/<module>/module.yaml
    definitions/<module>/shared/*.py              in every bundle of the module, as `shared`
    definitions/<module>/references/*.md          the law the module applies, one source a file (ADR-0041)
    definitions/<module>/<key>/definition.yaml
    definitions/<module>/<key>/forms/*.json
    definitions/<module>/<key>/rules/...
    definitions/<module>/<key>/guide.md
    definitions/<module>/<key>/cases/*.yaml        its test cases, run by the publish gate
    definitions/<module>/<key>/**/*.py             the bundle; never tests/, forms.py or host.py

Everything is checked here, at boot: a definition that cannot be run stops the process rather
than failing one case later. Nothing is imported: the Python is copied into a bundle the sandbox
runs.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app.functions.bundle import build_bundle
from app.kernel.cases import CaseError, check_cases
from app.kernel.procedures import ProcedureError, procedure_of
from app.kernel.workflows import Workflow

DEFINITIONS = Path(__file__).resolve().parents[2] / "definitions"
_FORMS = ("input", "settings", "result")
_REQUIRED_FUNCTIONS = ("compute", "check")
_KNOWN_FUNCTIONS = {"compute", "check", "letters"}
_TRIGGERS = {"manual", "schedule"}
_HOST_ONLY = {"forms.py", "host.py"}
# A definition's legal sources, in total: the builder reads them whole on every run (ADR-0041 D3).
MAX_REFERENCE_CHARS = 200_000
_REFERENCE_FIELDS = ("title", "cites", "url", "stand", "retrieved")


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


def read_definitions(module_dir: Path) -> list[dict]:
    """The content of every definition of one module, read from its files."""
    return [read_definition(p.parent, module_dir) for p in sorted(module_dir.glob("*/definition.yaml"))]


def read_definition(directory: Path, module_dir: Path) -> dict:
    """A definition's content: everything that makes it run, as plain JSON. The same form is
    stored as a version, checksummed, exported again, and turned into a workflow."""
    spec = _yaml(directory / "definition.yaml")
    where = f"definition {directory.relative_to(module_dir.parent)}"
    key = spec.get("key")
    if key != directory.name:
        raise DefinitionError(f"{where} declares key {key!r}, not its directory's name")

    forms = {}
    for name in _FORMS:
        path = directory / "forms" / f"{name}.json"
        try:
            forms[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DefinitionError(f"{where}: forms/{name}.json: {exc}") from exc
    assessment = directory / "forms" / "assessment.json"
    if assessment.is_file():  # what its assess step returns (ADR-0042 D2)
        try:
            forms["assessment"] = json.loads(assessment.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DefinitionError(f"{where}: forms/assessment.json: {exc}") from exc

    rules_spec = spec.get("rules") or {}
    guide = directory / "guide.md"
    sources: dict[str, str] = {}
    for path in sorted(directory.rglob("*.py")):
        relative = path.relative_to(directory)
        if relative.parts[0] == "tests" or "__pycache__" in relative.parts:
            continue
        if path.name in _HOST_ONLY:
            continue  # read on the host for the forms and the tests; the sandbox never needs it
        sources[relative.as_posix()] = path.read_text(encoding="utf-8")
    shared = module_dir / "shared"
    for path in sorted(shared.rglob("*.py")) if shared.is_dir() else ():
        if "__pycache__" not in path.parts:
            sources[f"shared/{path.relative_to(shared).as_posix()}"] = path.read_text(encoding="utf-8")

    content = {
        "key": str(key),
        "module": module_dir.name,
        "version": int(spec.get("version", 1)),
        "label": str(spec.get("label", key)),
        "summary": str(spec.get("summary", "")),
        "functions": list(spec.get("functions", ())),
        "forms": forms,
        "rules": _as_json(_yaml(directory / rules_spec.get("file", "rules/rules.yaml"))),
        "rules_name": str(rules_spec.get("name", "rules")),
        "guide": guide.read_text(encoding="utf-8") if guide.is_file() else "",
        "sources": sources,
    }
    if "procedure" in spec:  # stated: run as stated; absent: derived (app.kernel.procedures)
        content["procedure"] = spec["procedure"]
    if "triggers" in spec:  # stated: how a run may start; absent: by a person only (ADR-0040)
        content["triggers"] = spec["triggers"]
    if "tools" in spec:  # the calculators its assess step may call (ADR-0042 D3)
        content["tools"] = _as_json(spec["tools"])
    if "references" in spec:  # the law it applies, as the module ships it (ADR-0041 D2)
        content["references"] = _named_references(list(spec["references"]), module_dir, where)
    if (directory / "cases").is_dir():
        content["tests"] = [_case(path, directory, where) for path in sorted((directory / "cases").glob("*.yaml"))]
    check_content(content, where)
    return content


def module_references(module_dir: Path) -> dict[str, dict]:
    """The legal sources a module ships, by name (ADR-0041 D1): each a Markdown file whose header
    says what it is, where it was published, which consolidated version and when it was fetched."""
    found = {}
    for path in sorted((module_dir / "references").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, header, body = text.split("---\n", 2) if text.startswith("---\n") else ("", "", "")
        try:
            meta = yaml.safe_load(header) if header else None
        except yaml.YAMLError as exc:
            raise DefinitionError(f"{path}: its header is not YAML: {exc}") from exc
        if not isinstance(meta, dict) or any(f not in meta for f in _REFERENCE_FIELDS):
            raise DefinitionError(f"{path}: a source starts with a header naming {', '.join(_REFERENCE_FIELDS)}")
        found[path.stem] = {"name": path.stem, **{f: _as_json(meta[f]) for f in _REFERENCE_FIELDS}, "text": body.strip()}
    return found


def _named_references(names: list[str], module_dir: Path, where: str) -> list[dict]:
    shipped = module_references(module_dir)
    if missing := [n for n in names if n not in shipped]:
        raise DefinitionError(f"{where} applies sources its module does not ship: {', '.join(missing)}")
    return [shipped[n] for n in names]


def with_module_references(content: dict, module_dir: Path) -> dict:
    """`content` with the module's current text of each source it names: the law is the
    platform's, never a workspace's to edit (ADR-0038, ADR-0041 D2). A published version keeps
    the copy it was published with; a draft always carries what the module ships now."""
    if "references" not in content:
        return content
    names = [r.get("name") for r in content["references"] if isinstance(r, dict)]
    where = f"definition {content.get('key')!r}"
    return {**content, "references": _named_references(names, module_dir, where)}


def _case(path: Path, directory: Path, where: str) -> dict:
    """One test case, with its input in it: a version holds what it was tested on, not a path."""
    case = _yaml(path)
    if "input_file" in case:
        source = (directory / case.pop("input_file")).resolve()
        if directory.resolve() not in source.parents:
            raise DefinitionError(f"{where}: cases/{path.name} reads an input from outside the definition")
        case["input"] = _yaml(source)
    return _as_json({"name": path.stem, **case})


def checksum(content: dict) -> str:
    """What identifies a version's content: the same content always gives the same checksum."""
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_content(content: dict, where: str = "") -> None:
    """Refuse content that cannot run: whether read from files, stored, or about to be published."""
    where = where or f"definition {content.get('key')!r}"
    functions = content.get("functions") or []
    if missing := [f for f in _REQUIRED_FUNCTIONS if f not in functions]:
        raise DefinitionError(f"{where} has no {', '.join(missing)} function")
    if unknown := set(functions) - _KNOWN_FUNCTIONS:
        raise DefinitionError(f"{where} declares unknown functions: {sorted(unknown)}")
    sources = content.get("sources") or {}
    for name in functions:
        if f"functions/{name}.py" not in sources:
            raise DefinitionError(f"{where}: functions/{name}.py is missing")
    for name in _FORMS:
        if (content.get("forms") or {}).get(name, {}).get("type") != "object":
            raise DefinitionError(f"{where}: its {name} form is not an object form")
    if "triggers" in content:
        triggers = content["triggers"]
        if not isinstance(triggers, list) or not triggers or any(t not in _TRIGGERS for t in triggers):
            raise DefinitionError(f"{where}: triggers is a list of {sorted(_TRIGGERS)}, not {triggers!r}")
    _check_tools(content, where)
    _check_references(content.get("references", []), where)
    _check_knowledge(content.get("knowledge", []), where)
    read = sum(len(r.get("text", "")) for r in content.get("references", [])) + sum(
        len(k.get("content", "")) for k in content.get("knowledge", [])
    )
    if read > MAX_REFERENCE_CHARS:
        raise DefinitionError(
            f"{where}: {read:,} characters of law and documents, at most {MAX_REFERENCE_CHARS:,}; the "
            "builder reads every source whole"
        )
    if "version" not in (content.get("rules") or {}):
        raise DefinitionError(f"{where}: its rules have no version")
    try:
        procedure_of(content)
        if "tests" in content:
            check_cases(content["tests"])
    except (ProcedureError, CaseError) as exc:
        raise DefinitionError(f"{where}: {exc}") from exc


def _check_tools(content: dict, where: str) -> None:
    """A tool is a definition function a model may call from the assess step: a name it is
    called by, what it does, its parameters as a JSON Schema object, and the function in
    `functions/` that runs it, in the sandbox. Never one of compute, check or letters."""
    tools = content.get("tools", [])
    if not isinstance(tools, list):
        raise DefinitionError(f"{where}: tools is a list")
    names = [t.get("name") for t in tools if isinstance(t, dict)]
    if len(set(names)) != len(tools):
        raise DefinitionError(f"{where}: every tool has a name of its own")
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name.replace("_", "").isalnum():
            raise DefinitionError(f"{where}: a tool's name is letters, digits and _, not {name!r}")
        if not str(tool.get("description") or "").strip():
            raise DefinitionError(f"{where}: tool {name!r} says what it does")
        if not isinstance(tool.get("parameters"), dict) or tool["parameters"].get("type") != "object":
            raise DefinitionError(f"{where}: tool {name!r} has its parameters as an object schema")
        function = tool.get("function")
        if function in _KNOWN_FUNCTIONS or f"functions/{function}.py" not in (content.get("sources") or {}):
            raise DefinitionError(f"{where}: tool {name!r} runs functions/<its function>.py, not compute, check or letters")
    assesses = any(s.get("kind") == "assess" for s in content.get("procedure") or [])
    if assesses and (content.get("forms") or {}).get("assessment", {}).get("type") != "object":
        raise DefinitionError(f"{where}: an assess step returns its assessment form, and it has none")


def _check_references(references: Any, where: str) -> None:
    if not isinstance(references, list):
        raise DefinitionError(f"{where}: references is a list of sources")
    for ref in references:
        name = ref.get("name") if isinstance(ref, dict) else ref
        if not isinstance(ref, dict) or not str(ref.get("text") or "").strip():
            raise DefinitionError(f"{where}: source {name!r} has no text")
        if not isinstance(ref.get("cites"), list) or not ref["cites"]:
            raise DefinitionError(f"{where}: source {name!r} names how it is cited (cites)")


def _check_knowledge(documents: Any, where: str) -> None:
    """The workspace's own documents a definition works from, as pinned copies (ADR-0041 step 2)."""
    if not isinstance(documents, list) or any(
        not isinstance(d, dict) or not all(isinstance(d.get(f), str) and d[f] for f in ("id", "title", "sha", "content"))
        for d in documents
    ):
        raise DefinitionError(f"{where}: knowledge is a list of the workspace's documents, each with its text")


def workflow_from(
    content: dict,
    bundles: Path,
    version_id: str | None = None,
    gate: dict | None = None,
    owner: str | None = None,
) -> Workflow:
    """The runnable workflow for one definition's content. The only way one is made."""
    check_content(content)
    key, version = content["key"], int(content["version"])
    return Workflow(
        key=key,
        label=content["label"],
        summary=content["summary"],
        version=version,
        forms=content["forms"],
        rules=content["rules"],
        rules_version=f"{content['rules_name']} v{content['rules']['version']}",
        extraction_guide=content["guide"],
        bundle=build_bundle(
            content["sources"], bundles / f"{content['module']}-{key}-v{version}-{checksum(content)[:12]}"
        ),
        functions=tuple(content["functions"]),
        procedure=procedure_of(content),
        triggers=tuple(content.get("triggers", ("manual",))),
        check_confirmed_by=content.get("check_confirmed_by"),
        tools=tuple(content.get("tools") or ()),
        references=tuple(content.get("references") or ()),
        knowledge=tuple(content.get("knowledge") or ()),
        version_id=version_id,
        gate=gate,
        owner=owner,
    )


def load_definition(directory: Path, module_dir: Path, bundles: Path) -> Workflow:
    """A workflow straight from its files: for a definition's own tests and for tools."""
    return workflow_from(read_definition(directory, module_dir), bundles)


def bundles_dir() -> Path:
    """Where this process keeps its bundles: read-only to every function, rebuilt at boot."""
    return Path(tempfile.mkdtemp(prefix="laufwise-bundles-"))
