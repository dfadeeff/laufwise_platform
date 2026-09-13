"""The practice knowledge base, loaded from configuration (spec §5).

One file — `knowledge/muenchen.yaml` — is the source of truth for opening hours, the slot grid,
the calendars that may be booked, the price list, the short-notice threshold and the approved
sentences about money and insurance. Everything downstream reads it rather than restating it:

- `SandboxCalendar` builds its grid from `schedule`, so the 12:00–13:00 break is absent from the
  grid rather than filtered out of it;
- `_instructions()` renders `knowledge_block()` into the agent's prompt at call time, so a price
  change is a config edit and not a prompt review;
- `search_availability` resolves "morning" through `time_windows`.

That single origin is the point: an agent that quotes €69 and an agent that books a
`medizinische_fusspflege` slot must not be able to disagree about which service that is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

KNOWLEDGE_PATH = Path(__file__).parent / "knowledge" / "muenchen.yaml"


def _clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class Period:
    """One continuous stretch of opening hours. The gap between two periods is the break."""

    start: time
    end: time

    def contains(self, moment: time) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True)
class Window:
    """A spoken time preference ("morning") as a real clock range."""

    start: time
    end: time


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    price_eur: int
    duration_minutes: int
    # None until the practice maps our keys onto thevea's catalogue (spec §5). Carried into the
    # booking as-is so an unmapped service is visible in the trace instead of being invented.
    service_id: str | None
    agent_bookable: bool
    default: bool = False
    price_note: str | None = None
    handoff_reason: str | None = None

    @property
    def price(self) -> str:
        return f"€{self.price_eur}" + (f" {self.price_note}" if self.price_note else "")


@dataclass(frozen=True)
class Schedule:
    """When the practice is open, in what increments, and on which calendars.

    Owned by the destination the way thevea's working-hours rule lives in the thevea connector
    (ADR-0004) — nothing above the calendar has to know when this practice is open.
    """

    timezone: str
    open_weekdays: frozenset[int]
    periods: tuple[Period, ...]
    slot_minutes: int
    resources: tuple[str, ...]

    def is_open(self, day: date) -> bool:
        return day.weekday() in self.open_weekdays

    def starts_on(self, day: date) -> list[datetime]:
        """Every slot start the grid contains on `day`, in order.

        Built by walking each period separately, so a start inside the break is not produced and
        then rejected — it is never produced. A slot that would run past its period's end is not
        a slot: 11:45 is not offered in a period that closes at 12:00.
        """
        if not self.is_open(day):
            return []
        step = timedelta(minutes=self.slot_minutes)
        starts: list[datetime] = []
        for period in self.periods:
            cursor = datetime.combine(day, period.start)
            closes = datetime.combine(day, period.end)
            while cursor + step <= closes:
                starts.append(cursor)
                cursor += step
        return starts


@dataclass(frozen=True)
class Policy:
    short_notice_hours: int
    transcript_retention_days: int
    store_audio: bool
    consent_policy_id: str


@dataclass(frozen=True)
class Practice:
    updated: str
    location_id: str
    name: str
    street: str
    postcode: str
    city: str
    phone: str
    email: str
    payment: tuple[str, ...]
    schedule: Schedule
    time_windows: dict[str, Window]
    services: tuple[Service, ...]
    policy: Policy
    recipients: tuple[str, ...]
    phrases: dict[str, str]

    @property
    def address(self) -> str:
        return f"{self.street}, {self.postcode} {self.city}"

    def service(self, key: str) -> Service | None:
        return next((s for s in self.services if s.key == key), None)

    @property
    def default_service(self) -> Service:
        """What a caller who does not know what they need gets booked for (spec §4.2 step 3)."""
        return next(s for s in self.services if s.default)

    @property
    def bookable_services(self) -> tuple[Service, ...]:
        return tuple(s for s in self.services if s.agent_bookable)

    def window(self, name: str) -> Window | None:
        return self.time_windows.get(name)

    def is_short_notice(self, start: str, *, now: datetime | None = None) -> bool:
        """Whether cancelling or moving `start` now falls inside the notice period (spec §3.7).

        An unparseable start is treated as short notice: the warning is a sentence about a
        possible fee that the practice decides case by case, so saying it needlessly costs the
        caller nothing, and omitting it when it applied is the failure that matters.
        """
        try:
            when = datetime.fromisoformat(start)
        except ValueError:
            return True
        return when - (now or datetime.now()) < timedelta(hours=self.policy.short_notice_hours)

    def price_list(self) -> str:
        """The published list, with the phone-booking restriction stated as what it is.

        The wording matters and was got wrong once: a bare "not bookable by phone" tag was read
        by the agent as "we do not offer this", and it told a caller the practice does no home
        visits. The practice DOES — it just is not the agent's to book. So the line says the
        practice offers it and names who decides.
        """
        lines = []
        for service in self.services:
            suffix = (
                ""
                if service.agent_bookable
                else "  — the practice DOES offer this, but you may not book it on the phone: "
                f"{service.handoff_reason}. Quote the price and take a callback request."
            )
            lines.append(f"- {service.name}: {service.price}{suffix}")
        return "\n".join(lines)

    def knowledge_block(self) -> str:
        """The practice facts, rendered for the agent's instructions.

        Rendered at call time from the config rather than written into `base.md`, which is what
        keeps §5's rule true: the prompt carries the *rules* for using these facts, the config
        carries the facts.
        """
        hours = " und ".join(
            f"{p.start:%H:%M}–{p.end:%H:%M}" for p in self.schedule.periods
        )
        windows = ", ".join(
            f"{name} {w.start:%H:%M}–{w.end:%H:%M}" for name, w in self.time_windows.items()
        )
        return "\n".join(
            [
                f"Practice: {self.name}",
                f"Address: {self.address}",
                f"Phone: {self.phone}   Email: {self.email}",
                f"Opening hours: Monday–Friday {hours}; closed Saturday and Sunday.",
                "The 12:00–13:00 break is not bookable and is never offered.",
                f"Payment: {', '.join(self.payment)}.",
                f"Every appointment booked by phone is {self.schedule.slot_minutes} minutes.",
                f"Time windows: {windows}.",
                "",
                f"Price list (as published on {self.updated}):",
                self.price_list(),
                "",
                "Approved wordings — say these as written when they apply, do not paraphrase:",
                f"- Muster 13 / statutory insurance: {self.phrases['muster13']}",
                f"- Private prescription: {self.phrases['privatrezept']}",
                f"- Short-notice cancellation: {self.phrases['ausfallhonorar']}",
                f"- Offering a move instead of a cancellation: {self.phrases['offer_reschedule']}",
            ]
        )


def _parse(raw: dict[str, Any]) -> Practice:
    prac = raw["practice"]
    sched = raw["schedule"]
    pol = raw["policy"]
    return Practice(
        updated=str(raw["updated"]),
        location_id=raw["location_id"],
        name=prac["name"],
        street=prac["street"],
        postcode=str(prac["postcode"]),
        city=prac["city"],
        phone=prac["phone"],
        email=prac["email"],
        payment=tuple(prac["payment"]),
        schedule=Schedule(
            timezone=sched["timezone"],
            open_weekdays=frozenset(sched["open_weekdays"]),
            periods=tuple(
                Period(_clock(p["from"]), _clock(p["to"])) for p in sched["periods"]
            ),
            slot_minutes=int(sched["slot_minutes"]),
            resources=tuple(sched["resources"]),
        ),
        time_windows={
            name: Window(_clock(w["from"]), _clock(w["to"]))
            for name, w in raw["time_windows"].items()
        },
        services=tuple(
            Service(
                key=s["key"],
                name=s["name"],
                price_eur=int(s["price_eur"]),
                duration_minutes=int(s["duration_minutes"]),
                service_id=s.get("service_id"),
                agent_bookable=bool(s.get("agent_bookable", False)),
                default=bool(s.get("default", False)),
                price_note=s.get("price_note"),
                handoff_reason=s.get("handoff_reason"),
            )
            for s in raw["services"]
        ),
        policy=Policy(
            short_notice_hours=int(pol["short_notice_hours"]),
            transcript_retention_days=int(pol["transcript_retention_days"]),
            store_audio=bool(pol["store_audio"]),
            consent_policy_id=pol["consent_policy_id"],
        ),
        recipients=tuple(raw["notifications"]["recipients"]),
        phrases=dict(raw["phrases"]),
    )


@lru_cache(maxsize=None)
def load_practice(path: str | Path = KNOWLEDGE_PATH) -> Practice:
    """The practice knowledge base. Cached — it is configuration, read once per process."""
    return _parse(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
