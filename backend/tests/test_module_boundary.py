"""The kernel/module boundary (ADR-0022 D8, ADR-0028), checked on the source rather than by review.

- The kernel never imports a module: it loads the ones `INSTALLED` names, so deleting a module
  package and its line leaves the kernel importable.
- A module imports from `app` only through `app.kernel`, plus its own package.
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


def _app_imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return [n for n in names if n == "app" or n.startswith("app.")]


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


def test_a_module_reaches_the_kernel_only_through_app_kernel() -> None:
    offenders = []
    for path in _sources(MODULES):
        parts = path.relative_to(MODULES).parts
        own = f"app.modules.{parts[0]}" if len(parts) > 1 else "app.modules"
        for name in _app_imports(path):
            allowed = (
                name == "app.kernel"
                or name.startswith("app.kernel.")
                or name == own
                or name.startswith(own + ".")
            )
            if not allowed:
                offenders.append(f"{path.relative_to(BACKEND)} imports {name}")
    assert offenders == []


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
