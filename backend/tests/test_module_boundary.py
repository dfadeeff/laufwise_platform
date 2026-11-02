"""The platform/definition boundary (ADR-0022 D8, ADR-0028, ADR-0039), checked on the source.

- The platform never imports a definition: it reads `definitions/` as data and runs a
  definition's Python in the sandbox. Deleting `definitions/` leaves the platform importable.
- A definition's code imports only the standard library, the sandbox's `sdk`, its own packages
  and its module's `shared`: nothing from `app`, and no client that reaches a database, a
  network, a browser or a process. The sandbox enforces it at run time; this keeps the source
  honest too.
- Every shipped runbook belongs to exactly one module, so no template is deployable everywhere by
  accident; every template a module claims exists.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

from app.kernel.modules import Module, ModuleError, discover, installed, owner_of, validated

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
DEFINITIONS = BACKEND / "definitions"


# Clients that reach a database, a network, a browser or another process.
_IO = (
    "aiohttp", "anthropic", "asyncpg", "boto3", "ftplib", "http.client", "httpx", "imaplib",
    "openai", "playwright", "psycopg", "psycopg2", "requests", "selenium", "smtplib", "socket",
    "sqlalchemy", "subprocess", "supabase", "twilio", "urllib.request", "websockets",
)


def _imports(path: Path, root: Path = BACKEND) -> list[str]:
    """Every module `path` imports, relative imports resolved to absolute names — so
    `from ....db import repo` inside a module is seen as `app.db`, not skipped."""
    package = list(path.relative_to(root).parts[:-1])
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                names.append(node.module or "")
            else:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                names.append(".".join([*base, *([node.module] if node.module else [])]))
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def _app_imports(path: Path, root: Path = BACKEND) -> list[str]:
    return [n for n in _imports(path, root) if n == "app" or n.startswith("app.")]


def _io_imports(path: Path, root: Path = BACKEND) -> list[str]:
    return [
        n for n in _imports(path, root) if any(n == c or n.startswith(c + ".") for c in _IO)
    ]


def _sources(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _definition_packages(root: Path = DEFINITIONS) -> set[str]:
    """The top-level names definition code is imported by: each module's `shared`, each
    definition's own packages and `functions`."""
    names = {"shared", "functions"}
    for definition in root.glob("*/*/definition.yaml"):
        names |= {p.name for p in definition.parent.iterdir() if p.is_dir() and (p / "__init__.py").exists() or p.is_dir() and any(p.glob("*.py"))}
    return names - {"tests"}


def test_the_platform_never_imports_a_definition() -> None:
    packages = _definition_packages()
    offenders = [
        f"{path.relative_to(BACKEND)} imports {name}"
        for path in _sources(APP)
        if "guest" not in path.relative_to(APP).parts  # the sandbox's own files run inside
        for name in _imports(path)
        if name.split(".")[0] in packages or name.split(".")[0] == "definitions"
    ]
    assert offenders == []


def _definition_offenders(root: Path, backend: Path) -> list[str]:
    offenders = []
    allowed_roots = _definition_packages(root) | {"sdk"} | set(sys.stdlib_module_names) | {"__future__"}
    for path in _sources(root):
        relative = path.relative_to(root).parts
        if "tests" in relative or path.name == "conftest.py":
            continue  # tests run in-process and may use the platform's test tools
        # Host-side files: read on the host for the forms and the tests, never in the sandbox.
        is_spec = path.name in ("forms.py", "host.py")
        for name in _imports(path, backend):
            top = name.split(".")[0]
            if top == "app" or (top not in allowed_roots and not (is_spec and top == "yaml")):
                offenders.append(f"{path.relative_to(backend)} imports {name}")
        offenders += [f"{path.relative_to(backend)} does I/O via {n}" for n in _io_imports(path, backend)]
    return offenders


def test_a_definition_imports_only_the_standard_library_sdk_and_its_own_code() -> None:
    assert _definition_offenders(DEFINITIONS, BACKEND) == []


def test_the_boundary_check_sees_a_platform_import_and_an_io_client(tmp_path: Path) -> None:
    """The check itself, on a definition written to break it."""
    definition = tmp_path / "definitions" / "m" / "d"
    (definition / "d").mkdir(parents=True)
    (definition / "definition.yaml").write_text("key: d\n", encoding="utf-8")
    (definition / "d" / "flow.py").write_text(
        "from app.db import repo\nimport httpx\nfrom d import fine\nimport decimal\n", encoding="utf-8"
    )

    assert sorted(_definition_offenders(tmp_path / "definitions", tmp_path)) == [
        "definitions/m/d/d/flow.py does I/O via httpx",
        "definitions/m/d/d/flow.py imports app.db",
        "definitions/m/d/d/flow.py imports httpx",
    ]


def test_without_definitions_the_platform_has_no_modules_and_still_loads(tmp_path: Path) -> None:
    modules, keys = discover(tmp_path / "nothing here")
    assert (modules, keys) == ([], ())
    assert validated(modules, keys) == {}


def _runbook_names() -> set[str]:
    return {
        yaml.safe_load(p.read_text(encoding="utf-8"))["template"]
        for p in (BACKEND / "runbooks").glob("*.yaml")
    }


def test_every_shipped_runbook_belongs_to_exactly_one_module() -> None:
    assert {name: owner_of(name) for name in _runbook_names() if owner_of(name) is None} == {}


def test_every_template_a_module_claims_exists() -> None:
    claimed = {name for m in installed().values() for name in m.templates}
    assert claimed - _runbook_names() == set()


def _module(key: str, **overrides) -> Module:
    return Module(key=key, label=key, summary="", pitch=(), intro="", **overrides)


def test_two_modules_cannot_claim_one_template() -> None:
    with pytest.raises(ModuleError, match="claimed by a and b"):
        validated(
            [_module("a", templates=frozenset({"t"})), _module("b", templates=frozenset({"t"}))],
            ("a", "b"),
        )


def test_a_module_cannot_ask_for_a_surface_the_kernel_has_not_got() -> None:
    with pytest.raises(ModuleError, match="surfaces"):
        validated([_module("a", surfaces=frozenset({"teleport"}))], ("a",))


def test_a_module_must_declare_the_key_it_is_installed_under() -> None:
    with pytest.raises(ModuleError, match="declares itself"):
        validated([_module("b")], ("a",))


def test_every_definitions_forms_are_written_from_its_spec() -> None:
    """forms/*.json is what the platform reads; it must not drift from the definition's spec."""
    import subprocess

    done = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "write_forms.py"), "--check"],
        capture_output=True, text=True, cwd=BACKEND,
    )
    assert done.returncode == 0, done.stdout + done.stderr
