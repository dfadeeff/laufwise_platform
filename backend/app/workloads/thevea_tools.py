"""thevea write tools — the plugin side of the seam (ADR-0003 D6).

`book_appointment` writes through the TheveaClient (a distinct GraphQL mutation from the
availability read the postcondition re-queries — so verification is non-circular). The tool is
built bound to a resolved client; the engine's ToolRegistryAdapter calls it as `(provider, step)`.

The tool CLAIMS success when thevea accepts the write; whether the appointment actually persisted
is decided by the postcondition's independent re-query (`TheveaStateProvider`), never by this
return value — that separation is the whole governance point (PLATFORM_PLAN §9).
"""

from __future__ import annotations

from typing import Any, Callable

from laufwise.adapters.base import StepOutcome

from app.providers.thevea import TheveaClient, TheveaError


def thevea_tools(client: TheveaClient) -> dict[str, Callable[[Any, Any], StepOutcome]]:
    """Build the thevea tool registry bound to one account's client."""

    def _book_appointment(provider: Any, step: Any) -> StepOutcome:
        want_slot = _want_slot_from_step(step)
        try:
            client.book(want_slot)
        except TheveaError as exc:
            # A failed write is an unsuccessful outcome; the step's postcondition will also
            # independently show the slot was not booked.
            return StepOutcome(ok=False, note=f"thevea booking failed: {exc}")
        return StepOutcome(ok=True, note="thevea accepted the booking (claim)")

    return {"book_appointment": _book_appointment}


def _want_slot_from_step(step: Any) -> dict[str, Any]:
    """The appointment window to book, taken from the step's execute args (template-provided)."""
    execute = getattr(step, "execute", None)
    return dict(getattr(execute, "args", None) or {})