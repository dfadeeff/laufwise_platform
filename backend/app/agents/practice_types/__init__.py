"""Practice types: the starting point for an agent, as data (ADR-0016 D2).

One JSON file per type (`podiatry.json`, `dental.json`, ...) holds what practices of that kind have
in common: their usual treatments, appointment length, opening hours, and how the agent should
speak. Applying one fills a new agent's draft; the practice then adds what only it knows (name,
address, prices, recipients) and publishes as usual. Adding a type is adding a file.

Prices are always 0 here, which the agent reads out as "price on request". A template cannot know
a practice's prices, and a wrong one is a promise the practice has to honour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agents.config import AgentConfig

_DIRECTORY = Path(__file__).parent


@dataclass(frozen=True)
class PracticeType:
    key: str
    label: str
    description: str
    config: dict[str, Any]

    def apply(self, *, name: str, locale: str, practice_name: str = "") -> AgentConfig:
        """A new agent's draft: this type's defaults plus what the practice typed in."""
        return AgentConfig.model_validate(
            {**self.config, "name": name, "locale": locale, "practice_name": practice_name}
        )


@lru_cache(maxsize=1)
def load_practice_types() -> dict[str, PracticeType]:
    found: dict[str, PracticeType] = {}
    for path in sorted(_DIRECTORY.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        found[raw["key"]] = PracticeType(
            key=raw["key"], label=raw["label"], description=raw["description"], config=raw["config"]
        )
    return found
