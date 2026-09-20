"""Versioned customer configuration for the conversational workload, without credentials."""

from __future__ import annotations

import re
from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workloads.conversational.practice import (
    Practice,
    Schedule,
    Period,
    Service,
    Policy,
    Window,
)


class Treatment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    name: str = Field(min_length=1, max_length=160)
    price_eur: int = Field(default=0, ge=0, le=10000)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Receptionist", min_length=1, max_length=120)
    practice_name: str = Field(default="", max_length=160)
    street: str = Field(default="", max_length=200)
    postcode: str = Field(default="", max_length=20)
    city: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=200)
    recipients: list[str] = Field(default_factory=list, max_length=10)
    timezone: str = "Europe/Berlin"
    locale: Literal["de", "en", "ru", "ar"] = "de"
    greeting: str = Field(default="", max_length=1000)
    instructions: str = Field(
        default="Be welcoming, concise and ask one question at a time.", max_length=6000
    )
    voice_id: str = Field(default="", max_length=100)
    open_from: str = "09:00"
    open_until: str = "18:00"
    break_from: str = "12:00"
    break_until: str = "13:00"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], min_length=1)
    slot_minutes: Literal[15, 20, 30, 45, 60] = 30
    resources: list[str] = Field(
        default_factory=lambda: ["MA1", "MA2", "MA3"], min_length=1, max_length=20
    )
    treatments: list[Treatment] = Field(default_factory=list, max_length=50)
    consent_policy_id: str = Field(default="", max_length=200)
    booking_enabled: bool = True
    # Which capabilities this agent has. ``None`` means every skill on disk, which is what every
    # agent published before this field existed was already getting — so an old snapshot parses
    # and behaves identically. An empty list would mean "no capabilities at all", and a list that
    # secretly meant "all" when empty is the kind of cleverness that fails a review at 3am.
    skills: list[str] | None = Field(default=None, max_length=20)
    # How the call is heard and answered. "cascaded" is transcribe -> think -> synthesise, which
    # every published agent is running today and which the eval suite replays. "realtime" is one
    # speech-to-speech model: faster, interruptible, and the reason a caller stops noticing.
    voice_engine: Literal["cascaded", "realtime"] = "cascaded"
    realtime_voice: str = Field(default="marin", pattern=r"^[a-z]+$", max_length=40)
    # What a returning caller is told before anyone has checked who they are (ADR-0011 D2).
    # "off" is the default and is what every agent published before this field means.
    recall_policy: Literal["off", "greeting", "full"] = "off"
    recall_acknowledged: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid timezone") from exc
        return value

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, values):
        if any(not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", v) for v in values):
            raise ValueError("Enter valid notification email addresses")
        return values

    @field_validator("skills")
    @classmethod
    def valid_skills(cls, values):
        """Shape only — deliberately NOT checked against the skills on disk.

        ``instance_config()`` re-validates the pinned snapshot on every inbound call, so a skill
        renamed in a later release would turn this validator into a 500 on a published agent's
        live phone line. The catalogue check belongs in ``publish_issues()``, where a person is
        present to fix it.
        """
        if values is None:
            return values
        if not values:
            raise ValueError("Choose at least one capability")
        if len(set(values)) != len(values):
            raise ValueError("Capabilities must be unique")
        if any(not re.fullmatch(r"[a-z0-9_]+", v) for v in values):
            raise ValueError("Capability names are lowercase letters, digits and underscores")
        return values

    @model_validator(mode="after")
    def valid_schedule(self):
        clocks = [self.open_from, self.open_until, self.break_from, self.break_until]
        if any(v and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", v) for v in clocks):
            raise ValueError("Use HH:MM for opening hours")
        if not self.open_from or not self.open_until or self.open_from >= self.open_until:
            raise ValueError("Closing time must be after opening time")
        if bool(self.break_from) != bool(self.break_until):
            raise ValueError("Set both break times, or leave both empty")
        if (
            self.break_from
            and not self.open_from < self.break_from < self.break_until < self.open_until
        ):
            raise ValueError("The break must fall inside opening hours")
        if any(d not in range(7) for d in self.weekdays):
            raise ValueError("Weekdays must be between 0 and 6")
        if len(set(self.resources)) != len(self.resources) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+", r) for r in self.resources
        ):
            raise ValueError(
                "Calendar labels must be unique letters, numbers, hyphens or underscores"
            )
        if len({t.key for t in self.treatments}) != len(self.treatments):
            raise ValueError("Treatment keys must be unique")
        return self

    def publish_issues(self) -> list[str]:
        fields = {
            "Practice name": self.practice_name,
            "Street": self.street,
            "Postcode": self.postcode,
            "City": self.city,
            "Practice phone": self.phone,
        }
        issues = [
            f"Add {label.lower()} in Practice knowledge."
            for label, value in fields.items()
            if not value.strip()
        ]
        if not self.recipients:
            issues.append("Add a staff notification email in Phone & handoff.")
        if self.booking_enabled and not self.treatments:
            issues.append("Add at least one treatment in Practice knowledge.")
        if self.booking_enabled and not self.consent_policy_id.strip():
            issues.append("Add your approved privacy policy reference in Capabilities.")
        issues.extend(self._capability_issues())
        issues.extend(self._voice_issues())
        if self.recall_policy == "full" and not self.recall_acknowledged:
            issues.append(
                "Reading an appointment to a caller identified only by their phone number needs "
                "your explicit confirmation in Capabilities before this agent can go live."
            )
        return issues

    def _voice_issues(self) -> list[str]:
        """A control that does nothing is worse than a missing one — it reads as a promise."""
        if self.voice_engine != "realtime":
            return []
        issues = []
        if self.locale == "ar":
            issues.append(
                "Arabic runs on the standard voice engine. Switch the engine back in Voice & "
                "language, or choose another language."
            )
        if self.voice_id:
            issues.append(
                "A realtime agent speaks with its own voice. Clear the selected voice in Voice & "
                "language, or switch back to the standard engine."
            )
        return issues

    def _capability_issues(self) -> list[str]:
        """The catalogue check the field validator deliberately skips, run where a person is."""
        from app.workloads.conversational.skills import load_skills

        if self.skills is None:
            return []
        catalogue = {skill.name for skill in load_skills()}
        issues = [
            f"Remove the capability '{name}' in Capabilities — it no longer exists."
            for name in self.skills
            if name not in catalogue
        ]
        if not set(self.skills) & catalogue:
            issues.append("Switch on at least one capability in Capabilities.")
        elif self.booking_enabled and "book_appointment" not in self.skills:
            issues.append(
                "Booking is switched on but the booking capability is off. Turn one of them on "
                "or the other off in Capabilities."
            )
        return issues

    def to_practice(self) -> Practice:
        periods = (
            [(self.open_from, self.open_until)]
            if not self.break_from
            else [(self.open_from, self.break_from), (self.break_until, self.open_until)]
        )
        services = tuple(
            Service(
                t.key,
                t.name,
                t.price_eur,
                self.slot_minutes,
                None,
                self.booking_enabled,
                default=i == 0,
            )
            for i, t in enumerate(self.treatments)
        )
        # Empty drafts remain rehearsable without inheriting another practice's treatment data.
        if not services:
            services = (
                Service(
                    "appointment", "Appointment", 0, self.slot_minutes, None, False, default=True
                ),
            )
        return Practice(
            updated="customer configuration",
            location_id="practice",
            name=self.practice_name or "Your practice",
            street=self.street,
            postcode=self.postcode,
            city=self.city,
            phone=self.phone,
            email=self.email,
            payment=(),
            schedule=Schedule(
                self.timezone,
                frozenset(self.weekdays),
                tuple(Period(time.fromisoformat(a), time.fromisoformat(b)) for a, b in periods),
                self.slot_minutes,
                tuple(self.resources),
            ),
            time_windows={
                "morning": Window(time(6), time(12)),
                "midday": Window(time(11), time(14)),
                "afternoon": Window(time(12), time(16)),
                "evening": Window(time(16), time(23)),
                "any": Window(time(0), time(23, 59)),
            },
            services=services,
            policy=Policy(24, 30, False, self.consent_policy_id),
            recipients=tuple(self.recipients),
            phrases={
                "muster13": "Please ask the practice about insurance coverage.",
                "privatrezept": "Please ask the practice about reimbursement.",
                "ausfallhonorar": "The practice will explain any cancellation charges.",
                "offer_reschedule": "The practice can help you change your appointment.",
                "greeting": self.greeting,
            },
        )
