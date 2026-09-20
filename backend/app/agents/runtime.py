"""Resolve a single immutable voice snapshot before opening browser or phone media."""

from app.agents.config import AgentConfig
from app.agents.service import instance_config, StudioError
from app.providers.sandbox import SandboxCalendar
from app.workloads.conversational.calendar import resolve_calendar


async def prepare_voice(session, instance, *, rehearsal):
    if not getattr(instance, "runtime_config", None):
        if not rehearsal:
            raise StudioError("Move this legacy voice deployment into Studio before activating it.")
        config = AgentConfig()
    else:
        config = instance_config(instance)
    practice = config.to_practice()
    if rehearsal:
        calendar, kind = SandboxCalendar(practice), "sandbox"
    else:
        if instance.snapshot_kind != "published":
            raise StudioError("Only published revisions can answer phone calls.")
        calendar, kind = await resolve_calendar(session, instance, practice=practice)
        if kind != "thevea":
            raise StudioError("A live phone agent requires a real Thevea calendar.")
    return calendar, kind, config
