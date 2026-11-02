"""What a module declares about itself, and the registry the kernel builds from the installed ones.

A module is one vertical (ADR-0028 D1): the nouns its workflows share, its runbooks, and how the
Studio speaks in a workspace that runs it. A workspace runs one or more modules
(`workspace_module`); the kernel uses this registry to decide what that workspace may deploy and
which surfaces it offers. Nothing here knows what a patient or a tenancy is.

The installed modules are the directories under `definitions/` with a `module.yaml` (ADR-0039):
data, read at boot, never imported. Adding one is a directory, not a line of platform code.

A module's workflows are its *published* definition versions (ADR-0039 step 2b): loaded from the
database at boot and again whenever one is published (`app.definitions.service`), held here as
the registry the routes read. `publish_from_files` fills it straight from the files instead, for
a process without a database (tests, tools).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.kernel.definitions import (
    DEFINITIONS,
    bundles_dir,
    module_manifests,
    read_definitions,
    workflow_from,
)
from app.kernel.workflows import Workflow

# Where the installed modules are read from. The platform's own tests point it at
# tests/fixtures/definitions (tests/conftest.py), so they pass without the shipped templates.
ROOT = DEFINITIONS

# The published workflows by module key. Empty until a publish fills it.
_published: dict[str, tuple[Workflow, ...]] = {}

# Kernel surfaces a module can switch on for its workspaces. The kernel owns the list: a surface is
# kernel code, a module only says it wants it.
SURFACES = frozenset({"voice_agents"})


class ModuleError(Exception):
    """An installed module declares something the kernel cannot accept. Raised at load, so a bad
    module stops the process at boot rather than misbehaving for one workspace later."""


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    # One sentence, shown when a new workspace chooses what it is for.
    summary: str
    # The Studio rail's footer: a title, then a line or two.
    pitch: tuple[str, ...]
    # Under the Agents heading.
    intro: str
    # The runbook templates this module owns, by name. Deploying one needs the module.
    templates: frozenset[str] = field(default_factory=frozenset)
    surfaces: frozenset[str] = field(default_factory=frozenset)
    # Workflows a workspace can create workflow agents from (ADR-0030).
    workflows: tuple[Workflow, ...] = ()

    def public(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "summary": self.summary,
            "pitch": list(self.pitch),
            "intro": self.intro,
            "templates": sorted(self.templates),
            "surfaces": sorted(self.surfaces),
            "workflows": [w.public() for w in self.workflows],
        }


def set_published(workflows: dict[str, tuple[Workflow, ...]]) -> None:
    """Replace the published workflows, by module key. Called at boot and on every publish."""
    global _published
    _published = dict(workflows)
    _installed.cache_clear()


def publish_from_files(root: Path | None = None) -> None:
    """Fill the registry from the definitions' files, as a seed would put them in the database:
    for a process without one. Not a second source of truth: the same content, the same
    `workflow_from`."""
    bundles = bundles_dir()
    by_module: dict[str, list[Workflow]] = {}
    for directory, _ in module_manifests(root or ROOT):
        for content in read_definitions(directory):
            by_module.setdefault(content["module"], []).append(workflow_from(content, bundles))
    set_published({k: tuple(v) for k, v in by_module.items()})


def discover(root: Path | None = None) -> tuple[list[Module], tuple[str, ...]]:
    """The modules under `root`, with their published workflows, and the directory names they
    were found under."""
    modules, keys = [], []
    for directory, manifest in module_manifests(root or ROOT):
        modules.append(
            Module(
                key=str(manifest.get("key", "")),
                label=str(manifest.get("label", "")),
                summary=str(manifest.get("summary", "")),
                pitch=tuple(manifest.get("pitch", ())),
                intro=str(manifest.get("intro", "")),
                templates=frozenset(manifest.get("templates", ())),
                surfaces=frozenset(manifest.get("surfaces", ())),
                workflows=_published.get(directory.name, ()),
            )
        )
        keys.append(directory.name)
    return modules, tuple(keys)


@lru_cache
def _installed(root: Path) -> dict[str, Module]:
    return validated(*discover(root))


def installed() -> dict[str, Module]:
    """The installed modules by key, validated together, in the order a workspace is offered them."""
    return _installed(ROOT)


def validated(modules: list[Module], keys: tuple[str, ...]) -> dict[str, Module]:
    by_key: dict[str, Module] = {}
    owners: dict[str, str] = {}
    workflows: dict[str, str] = {}
    for key, module in zip(keys, modules):
        if module.key != key:
            raise ModuleError(f"definitions/{key} declares itself as {module.key!r}")
        if unknown := module.surfaces - SURFACES:
            raise ModuleError(f"{key} asks for surfaces the kernel has not got: {sorted(unknown)}")
        for name in module.templates:
            if name in owners:
                raise ModuleError(f"template {name!r} is claimed by {owners[name]} and {key}")
            owners[name] = key
        for workflow in module.workflows:
            if workflow.key in workflows:
                raise ModuleError(
                    f"workflow {workflow.key!r} is offered by {workflows[workflow.key]} and {key}"
                )
            workflows[workflow.key] = key
        by_key[key] = module
    return by_key


def owner_of(template: str) -> str | None:
    """The module that owns a template, or None for one no module claims (Studio-authored)."""
    return next((m.key for m in installed().values() if template in m.templates), None)


def deploy_refusal(template: str, running: list[Module]) -> str | None:
    """Why a workspace running `running` may not deploy `template`, or None if it may. A template
    no module claims is not restricted: it was authored in the Studio, not shipped by a module."""
    owner = owner_of(template)
    if owner is None or owner in {m.key for m in running}:
        return None
    return (
        f"'{template}' comes with the {installed()[owner].label} module, which this workspace "
        "does not run."
    )


def offers(surface: str, running: list[Module]) -> bool:
    return any(surface in m.surfaces for m in running)


def providing(surface: str) -> Module | None:
    """The installed module that brings `surface` to a workspace: the first in offering order.
    A workspace chooses a kind of agent, not a module (ADR-0031); creating one adds the module."""
    return next((m for m in installed().values() if surface in m.surfaces), None)


def workflow(key: str) -> tuple[Module, Workflow] | None:
    """The module offering workflow `key`, and the workflow."""
    for module in installed().values():
        for w in module.workflows:
            if w.key == key:
                return module, w
    return None
