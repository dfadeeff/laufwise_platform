"""What a module declares about itself, and the registry the kernel builds from the installed ones.

A module is one vertical (ADR-0028 D1): the nouns its workflows share, its runbooks, and how the
Studio speaks in a workspace that runs it. A workspace runs one or more modules
(`workspace_module`); the kernel uses this registry to decide what that workspace may deploy and
which surfaces it offers. Nothing here knows what a patient or a tenancy is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib import import_module

# The modules this deployment ships, in the order a new workspace is offered them. Adding one is a
# package under `app/modules/` and a line here.
INSTALLED = ("praxis", "immobilien")

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

    def public(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "summary": self.summary,
            "pitch": list(self.pitch),
            "intro": self.intro,
            "templates": sorted(self.templates),
            "surfaces": sorted(self.surfaces),
        }


@lru_cache
def installed() -> dict[str, Module]:
    """The installed modules by key, validated together."""
    return validated([import_module(f"app.modules.{key}").MODULE for key in INSTALLED], INSTALLED)


def validated(modules: list[Module], keys: tuple[str, ...]) -> dict[str, Module]:
    by_key: dict[str, Module] = {}
    owners: dict[str, str] = {}
    for key, module in zip(keys, modules):
        if module.key != key:
            raise ModuleError(f"app.modules.{key} declares itself as {module.key!r}")
        if unknown := module.surfaces - SURFACES:
            raise ModuleError(f"{key} asks for surfaces the kernel has not got: {sorted(unknown)}")
        for name in module.templates:
            if name in owners:
                raise ModuleError(f"template {name!r} is claimed by {owners[name]} and {key}")
            owners[name] = key
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
