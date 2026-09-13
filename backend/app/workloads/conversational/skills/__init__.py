"""Skills — the unit of the agent you can reason about, review and swap.

Everything domain-specific about the agent lives in `skills/<name>/`: a `skill.json` naming its
tools and how it is routed to, and a `skill.md` holding its Purpose · Scope · Constraints ·
Behavior · Success. `prompts/base.md` keeps only what is true of every call — who the agent is,
how it speaks, what it must never do, and which skill handles what.

Why this rather than one prompt: a single file that knows about availability, identity checks,
cancellation notices, insurance wording and prices is a file nobody can review a change to. When
booking drifts you cannot tell whether the cause was a booking rule or the paragraph about
Ausfallhonorar three screens down. Split, each skill is a page you can read in one go, and its
tools, prompt and tags describe one domain (`conversational-agents` skill; ADR-0002).

**All skills are loaded at once, deliberately.** Multi-skill mode — one active skill, rebuilt
prompt, `switch_skill` — earns its keep when an agent spans genuinely unrelated domains. Booking,
changing and answering questions about the same practice are one domain seen from three angles,
and a caller moves between them mid-sentence ("what does it cost, and can I come Tuesday?"). Made
exclusive, that sentence needs a skill switch to answer, which buys confusion rather than focus.

The tool ALLOWLIST is still per skill and still enforced: `tool_specs()` returns exactly the tools
the loaded skills name, so a tool no skill claims is not callable by anyone. That is what keeps
the split honest rather than cosmetic — the skills own their tools, not just their prose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).parent

# Reachable from every skill, because a caller can ask for a person at any moment and "the
# practice will ring you back" is never the wrong answer. Wonderful's agents keep the same shape:
# transfer and end-call are global tools, never owned by one skill.
GLOBAL_TOOLS: tuple[str, ...] = ("create_callback_request",)


@dataclass(frozen=True)
class Skill:
    name: str
    display_name: str
    description: str
    tools: tuple[str, ...]
    tags: tuple[str, ...]
    prompt: str

    @property
    def is_state_changing(self) -> bool:
        """Read-only and state-changing flows are separated so the risky half can carry stricter
        review and its own evals — the reason the split is by outcome and not by mechanics."""
        return "state_changing" in self.tags


def _load(directory: Path) -> Skill:
    manifest = json.loads((directory / "skill.json").read_text(encoding="utf-8"))
    prompt_file = manifest.get("prompts", {}).get("skill", "skill.md")
    return Skill(
        name=manifest["name"],
        display_name=manifest["display_name"],
        description=manifest["description"],
        tools=tuple(manifest.get("tools", [])),
        tags=tuple(manifest.get("tags", [])),
        prompt=(directory / prompt_file).read_text(encoding="utf-8").strip(),
    )


@lru_cache(maxsize=1)
def load_skills() -> tuple[Skill, ...]:
    """Every skill in the directory, in a stable order.

    Sorted by name rather than by filesystem order so the assembled prompt is byte-identical
    between machines — the eval report's `prompt_sha` is only meaningful if it is.
    """
    found = sorted(
        (path.parent for path in SKILLS_DIR.glob("*/skill.json")), key=lambda p: p.name
    )
    return tuple(_load(directory) for directory in found)


def allowed_tools() -> tuple[str, ...]:
    """The tools the loaded skills claim, plus the global ones. The agent gets these and no more.

    A tool that no skill names is unreachable — which is the difference between skills as an
    organising idea and skills as a boundary.
    """
    named: list[str] = list(GLOBAL_TOOLS)
    for skill in load_skills():
        named.extend(tool for tool in skill.tools if tool not in named)
    return tuple(named)


def routing_block() -> str:
    """The one paragraph the base prompt needs about skills: which one handles what.

    A skill's `description` is written as a routing signal — "an appointment the caller already
    has" — so this is generated from the manifests rather than restated by hand, and a skill that
    is added or renamed cannot fall out of sync with the prompt that routes to it.
    """
    lines = [f"- **{s.display_name}** — {s.description}" for s in load_skills()]
    return "\n".join(lines)


def skill_prompts() -> str:
    """Every skill's prompt, in load order, under one heading."""
    return "\n\n---\n\n".join(skill.prompt for skill in load_skills())
