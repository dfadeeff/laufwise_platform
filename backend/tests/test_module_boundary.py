"""The kernel/module boundary (ADR-0022 D8, ADR-0028), checked on the source rather than by review.

- The kernel never imports a module: it loads the ones `INSTALLED` names, so deleting a module
  package and its line leaves the kernel importable.
- A module imports from `app` only through `app.kernel`, plus its own package.
- A module's code does no I/O of its own (ADR-0022 D4, ADR-0030 D2): no database, network,
  browser or process client, except an external system's adapter under a module's `systems/`.
  Acting on the outside world is a governed run, never a module function (red flags A1, C10).
- Every shipped runbook belongs to exactly one module, so no template is deployable everywhere by
  accident; every template a module claims exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from app.kernel.modules import Module, ModuleError, installed, owner_of, validated

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
MODULES = APP / "modules"


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


def test_the_kernel_never_imports_a_module() -> None:
    offenders = [
        f"{path.relative_to(BACKEND)} imports {name}"
        for path in _sources(APP)
        if MODULES not in path.parents
        for name in _app_imports(path)
        if name == "app.modules" or name.startswith("app.modules.")
    ]
    assert offenders == []


def _module_offenders(modules: Path, root: Path) -> list[str]:
    offenders = []
    for path in _sources(modules):
        parts = path.relative_to(modules).parts
        own = f"app.modules.{parts[0]}" if len(parts) > 1 else "app.modules"
        for name in _app_imports(path, root):
            allowed = (
                name == "app.kernel"
                or name.startswith("app.kernel.")
                or name == own
                or name.startswith(own + ".")
            )
            if not allowed:
                offenders.append(f"{path.relative_to(root)} imports {name}")
        if "systems" not in parts[1:-1]:
            offenders += [f"{path.relative_to(root)} does I/O via {n}" for n in _io_imports(path, root)]
    return offenders


def test_a_module_reaches_the_kernel_only_through_app_kernel_and_does_no_io() -> None:
    assert _module_offenders(MODULES, BACKEND) == []


def test_the_boundary_check_sees_a_relative_escape_and_an_io_client(tmp_path: Path) -> None:
    """The check itself, on a module written to break it: before red flag C10 was fixed, a
    relative import out of the module was skipped, and I/O was not looked at at all."""
    workflow = tmp_path / "app" / "modules" / "demo" / "work"
    workflow.mkdir(parents=True)
    (workflow / "flow.py").write_text(
        "from ...demo import fine\nfrom ....db import repo\nimport httpx\n", encoding="utf-8"
    )
    systems = tmp_path / "app" / "modules" / "demo" / "systems"
    systems.mkdir()
    (systems / "portal.py").write_text("import httpx\n", encoding="utf-8")

    assert sorted(_module_offenders(tmp_path / "app" / "modules", tmp_path)) == [
        "app/modules/demo/work/flow.py does I/O via httpx",
        "app/modules/demo/work/flow.py imports app.db",
    ]


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
