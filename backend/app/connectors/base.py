"""The calendar-connector abstraction (ADR-0004).

Both systems in a governed import are modelled as connectors so the runtime stays
domain-agnostic (PLATFORM_PLAN: runbook is data, agent is a plugin). Two capability protocols:

- `SourceCalendar` — read-only: enumerate + fetch appointments to copy (e.g. healthyfeet).
- `DestinationCalendar` — read + create ONLY (e.g. thevea). **There is deliberately no update or
  delete method** — append-only / never-replace (ADR-0004 D7) is enforced by the fact that the
  capability does not exist, not by convention.

A connector talks to a real system with credentials; concrete ones (thevea GraphQL, healthyfeet
REST) live next to this file and are built via `build_connector`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Appointment:
    """A calendar appointment in transit between systems. `ref` is the SOURCE identity — the
    stable key used to (a) tag the created destination appointment and (b) check idempotency."""

    ref: str
    start: str
    end: str | None = None
    type: str | None = None
    patient: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceCalendar(Protocol):
    def list_appointments(self, window: dict[str, Any]) -> list[Appointment]:
        """Enumerate the appointments to import for a window (the orchestration work-list)."""
        ...

    def get_appointment(self, ref: str) -> Appointment | None:
        """Fetch one appointment by its source id (re-grounds each governed run)."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class DestinationCalendar(Protocol):
    def find_appointment(self, ref: str) -> Appointment | None:
        """Return the destination appointment previously imported for this source ref, if any.
        Backs both the idempotency precondition and the post-write verification."""
        ...

    def create_appointment(self, appt: Appointment) -> None:
        """Append an appointment. The ONLY write capability — no update/delete exists (D7)."""
        ...

    def close(self) -> None: ...


def build_connector(adapter: str, base_url: str, credentials: dict[str, str]) -> Any:
    """Construct a connector by adapter name. Imported lazily to avoid import cycles and to keep
    httpx/connector deps out of modules that only need the protocols."""
    if adapter == "thevea":
        from app.providers.thevea import TheveaConnector

        return TheveaConnector(base_url, credentials.get("username", ""), credentials.get("password", ""))
    if adapter == "healthyfeet":
        from app.providers.healthyfeet import HealthyfeetConnector

        return HealthyfeetConnector(base_url, credentials.get("username", ""), credentials.get("password", ""))
    raise ValueError(f"unknown connector adapter {adapter!r}")