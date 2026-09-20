"""What an agent can actually do — answered once, for the runtime and the Studio alike.

There were two places that decided an agent's powers: the skills' own tool allowlist, and a pair
of `config` checks buried in the pipeline builder. Two places is one too many. A practice that
switches a capability off in the Studio has to be able to trust that the model was never told the
capability exists, and the only way to make that checkable is for the screen and the pipeline to
read the same answer from the same function.

The rule the shape encodes: a capability can be taken away here, never granted. `booking_enabled`
and the skill list can therefore disagree without the runtime becoming ambiguous — whichever says
"no" wins — while the publish gate (`AgentConfig.publish_issues`) refuses the contradiction in the
one place a person is present to resolve it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.workloads.conversational.skills import Skill, allowed_tools, selected_skills

# Changing or cancelling an appointment is gated on the destination supporting it (ADR-0008) and
# on the reviewed prompt that covers the notices. Until a Studio agent is published against a
# calendar that implements the lifecycle capability, these stay off for configured agents — the
# same restriction the pipeline used to hard-code, now stated once, in the open.
_LIFECYCLE_TOOLS = ("cancel_appointment", "reschedule_appointment")

# The tools that only make sense when something can actually be booked. They are claimed by more
# than one skill, so switching booking off has to remove them by name as well as removing the
# booking skill — otherwise an agent that cannot book still collects a name, a birth date and a
# confirmation, and the caller hears a promise nothing can keep.
_BOOKING_TOOLS = (
    "search_availability",
    "appointment_set_details",
    "appointment_confirm",
    "appointment_book",
)


@dataclass(frozen=True)
class Capabilities:
    """The skills whose prompts are assembled, and the exact tools the model may call."""

    skills: tuple[Skill, ...]
    tools: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(skill.name for skill in self.skills)


def resolve(config=None) -> Capabilities:
    """The capabilities of one agent. No config (the YAML practice, the eval runner) means all."""
    if config is None:
        return Capabilities(skills=selected_skills(), tools=allowed_tools())

    catalogue = {skill.name for skill in selected_skills()}
    # An unknown name is dropped rather than raised: a skill renamed in a later release must not
    # take a published agent's phone line down. The publish gate already told the practice.
    chosen = catalogue if config.skills is None else {n for n in config.skills if n in catalogue}
    if not config.booking_enabled:
        chosen = chosen - {"book_appointment"}

    enabled = frozenset(chosen)
    withheld = set(_LIFECYCLE_TOOLS) | (set() if config.booking_enabled else set(_BOOKING_TOOLS))
    tools = tuple(t for t in allowed_tools(enabled) if t not in withheld)
    return Capabilities(skills=selected_skills(enabled), tools=tools)
