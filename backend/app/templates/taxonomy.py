"""Compatibility view of the product category and runtime driver.

Published contracts keep their existing ``agent_class`` value.  These derived values let the
product present two categories without rewriting pinned production templates.
"""

from __future__ import annotations

from typing import Literal

from app.templates.contract import AgentClass

AgentCategory = Literal["operational", "conversational"]
AgentDriver = Literal["workflow", "conversation"]


def category_for(agent_class: AgentClass) -> AgentCategory:
    return "conversational" if agent_class == "conversational" else "operational"


def driver_for(agent_class: AgentClass) -> AgentDriver:
    return "conversation" if agent_class == "conversational" else "workflow"
