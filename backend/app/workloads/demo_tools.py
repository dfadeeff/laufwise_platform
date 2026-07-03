"""Demo tool implementations for the v1 in-memory workload.

Tool implementations are workload code (the plugin side of the seam) — the control plane wires
them in but must not know their vocabulary. Each impl writes to the provider-backed store
through its own code path; the runbook's declared outcome is never copied into state, so the
postcondition verifies what the tool actually did (PLATFORM_PLAN §9).
"""

from __future__ import annotations

from laufwise.adapters.base import StepOutcome


def _book_appointment(provider, step) -> StepOutcome:
    """Demo calendar write. A case may set `calendar.write_fails` to simulate a system of
    record that accepts the write but never persists it — the tool still CLAIMS success, and
    the postcondition's re-query is what catches the lie (REJECT)."""
    calendar = provider.query("calendar").value or {}
    if calendar.get("write_fails"):
        return StepOutcome(ok=True, note="calendar accepted the write (claim); nothing persisted")
    provider.apply({"calendar": {**calendar, "booking_confirmed": True}})
    return StepOutcome(ok=True, note="booked")


DEMO_TOOLS = {
    "book_appointment": _book_appointment,
}