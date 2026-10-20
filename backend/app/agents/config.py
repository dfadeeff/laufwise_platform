"""Versioned customer configuration for the conversational workload, without credentials."""

from __future__ import annotations

import re
import unicodedata
from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.workloads.conversational.practice import (
    GENERIC_APPOINTMENT,
    Practice,
    Schedule,
    Period,
    Service,
    Policy,
    WebsitePrice as WebsitePriceFact,
    PaymentCategory,
    Question,
    Window,
)


class WebsitePrice(BaseModel):
    """A treatment's price as the practice website shows it, put on the config at the start of a
    call from the workspace's price list (ADR-0032). Never pinned at publish, never typed in the
    Studio: a call that starts without it has no verified price for that treatment."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=40)  # as written: "55 €", "ab 69 €"
    amount: str = Field(max_length=12)
    kind: Literal["fixed", "from", "package"] = "fixed"
    detail: str = Field(default="", max_length=400)
    as_of: str = Field(max_length=10)  # the last day the page showed it, YYYY-MM-DD
    # The last read of the page failed, or no longer found this row: say it is the last known price.
    stale: bool = False


class Treatment(BaseModel):
    """A service as a published agent knows it: the workspace catalogue, pinned at publish
    (ADR-0025). Snapshots from before the catalogue carry only key, name and price."""

    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    name: str = Field(min_length=1, max_length=160)
    price_eur: int = Field(default=0, ge=0, le=10000)
    # The calendar system's own id for it (Thevea: price-list position). Without it the service is
    # never booked by phone: the release gate of the Mia specification v3.
    external_id: str = Field(default="", max_length=80)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    bookable: bool = True
    # Offered when the caller cannot say what they need, instead of picking a treatment for them.
    consultation: bool = False
    # The payment categories it may be booked with, by key (ADR-0027). Empty: any.
    payment_categories: list[str] = Field(default_factory=list, max_length=10)
    # Its row on the practice website's price list, by name (ADR-0032). Empty: no website price.
    price_label: str = Field(default="", max_length=200)
    website_price: WebsitePrice | None = None

    @property
    def phone_bookable(self) -> bool:
        return self.bookable and bool(self.external_id.strip())


class BookingQuestion(BaseModel):
    """A question the practice wants asked before a booking (ADR-0020).

    `label` is how the answer appears in the appointment's note ("Behandlung: Hornhaut"); `ask`
    is what the agent asks. A required question must be answered before the booking is written.
    """

    model_config = ConfigDict(extra="forbid")
    label: str = Field(max_length=40)
    ask: str = Field(max_length=300)
    required: bool = True


class PaymentOption(BaseModel):
    """One way a treatment is paid for, as the practice tells them apart (ADR-0027)."""

    model_config = ConfigDict(extra="forbid")
    # Given once from the first name and then kept: services refer to it. Empty: derived here.
    key: str = Field(default="", pattern=r"^[a-z0-9_]*$", max_length=40)
    label: str = Field(max_length=80)
    note: str = Field(default="", max_length=300)


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
    # How the practice appears in the call summaries the platform sends (ADR-0024). The platform's
    # own address is the sender; these are the name shown and where a reply goes. Empty: the
    # practice's name and `email`.
    notification_sender_name: str = Field(default="", max_length=120)
    notification_reply_to: str = Field(default="", max_length=200)
    # Where a caller who asks for a person is put through to, during opening hours. Empty means no
    # transfer at all — the agent takes a callback, as every agent published before this field did.
    transfer_number: str = Field(default="", max_length=16)
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
    # Asked before every booking; the answers go into the appointment's note (ADR-0020).
    booking_questions: list[BookingQuestion] = Field(default_factory=list, max_length=10)
    # How a treatment is paid for (ADR-0027). Set: every booking needs one, compatible with its
    # service. Empty: the agent does not ask, as before.
    payment_question: str = Field(default="", max_length=300)
    payment_categories: list[PaymentOption] = Field(default_factory=list, max_length=6)
    consent_policy_id: str = Field(default="", max_length=200)
    # How long a call's transcript is kept before the daily sweep deletes it. Callers are told this
    # number (spec §7), so it is the practice's to choose; 30 is what every agent had before.
    transcript_retention_days: int = Field(default=30, ge=1, le=365)
    # Cancelling or moving by phone (ADR-0021): free until this many hours before the appointment;
    # inside it nothing is changed by phone, the caller hears `cancellation_policy` and staff call
    # back. 24 is what every agent had before these fields existed.
    cancellation_free_hours: int = Field(default=24, ge=0, le=168)
    cancellation_policy: str = Field(default="", max_length=500)
    # The workspace documents this agent knows (ADR-0017), in the order it reads them. Ids, not
    # content: publishing copies the content into the snapshot.
    knowledge_ids: list[str] = Field(default_factory=list, max_length=20)
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

    @field_validator("notification_reply_to")
    @classmethod
    def valid_reply_to(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip()):
            raise ValueError("Enter a valid reply-to email address")
        return value.strip()

    @field_validator("transfer_number")
    @classmethod
    def valid_transfer_number(cls, value):
        if value and not re.fullmatch(r"\+[1-9]\d{6,14}", value):
            raise ValueError("Enter the transfer number in international format, e.g. +4989123456")
        return value

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
        if any(not q.label.strip() or not q.ask.strip() for q in self.booking_questions):
            raise ValueError("Give each booking question a label and what the agent asks")
        labels = [q.label.strip().lower() for q in self.booking_questions]
        if len(set(labels)) != len(labels):
            raise ValueError("Each booking question needs its own label")
        if any(not c.label.strip() for c in self.payment_categories):
            raise ValueError("Give each payment category a name")
        for c in self.payment_categories:
            if not c.key:
                plain = unicodedata.normalize("NFKD", c.label.lower().replace("ß", "ss"))
                c.key = re.sub(r"[^a-z0-9]+", "_", plain.encode("ascii", "ignore").decode()).strip(
                    "_"
                )[:40] or "category"
        if len({c.key for c in self.payment_categories}) != len(self.payment_categories):
            raise ValueError("Each payment category needs its own name")
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
            f"Add {label.lower()} in Knowledge base."
            for label, value in fields.items()
            if not value.strip()
        ]
        if not self.recipients:
            issues.append("Add a staff notification email in Phone & handoff.")
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
        # The catalogue as pinned at publish (ADR-0025). A service reaches the booking tool only if
        # it passed the release gate: switched on AND linked to the calendar system's own id. The
        # "default" is the practice's consultation, offered when a caller cannot say what they
        # need — never a treatment picked for them (Mia spec v3: no generic fallback).
        consultation = next((t.key for t in self.treatments if t.consultation), None)
        services = tuple(
            Service(
                t.key,
                t.name,
                t.price_eur,
                t.duration_minutes or self.slot_minutes,
                t.external_id or None,
                self.booking_enabled and t.phone_bookable,
                default=t.key == consultation if consultation else i == 0,
                handoff_reason=(
                    None if t.phone_bookable else "it is not set up for booking by phone yet"
                ),
                aliases=tuple(w for said in t.aliases.values() for w in said),
                consultation=t.consultation,
                payment_categories=tuple(t.payment_categories),
                price_label=t.price_label,
                website_price=(
                    WebsitePriceFact(**t.website_price.model_dump()) if t.website_price else None
                ),
            )
            for i, t in enumerate(self.treatments)
        )
        # No treatment list: the practice's services and prices live in its documents, and a
        # caller books one plain appointment of the configured length.
        if not services:
            services = (
                Service(
                    GENERIC_APPOINTMENT,
                    "Termin" if self.locale == "de" else "Appointment",
                    0,
                    self.slot_minutes,
                    None,
                    self.booking_enabled,
                    default=True,
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
            policy=Policy(
                self.cancellation_free_hours,
                self.transcript_retention_days,
                False,
                self.consent_policy_id,
            ),
            recipients=tuple(self.recipients),
            notify_name=self.notification_sender_name.strip(),
            notify_reply_to=self.notification_reply_to,
            phrases={
                "muster13": "Please ask the practice about insurance coverage.",
                "privatrezept": "Please ask the practice about reimbursement.",
                "ausfallhonorar": self.cancellation_policy.strip()
                or "The practice will explain any cancellation charges.",
                "offer_reschedule": "The practice can help you change your appointment.",
                "greeting": self.greeting,
            },
            questions=tuple(
                Question(q.label.strip(), q.ask.strip(), q.required) for q in self.booking_questions
            ),
            payment_categories=tuple(
                PaymentCategory(c.key, c.label.strip(), c.note.strip())
                for c in self.payment_categories
            ),
            payment_question=self.payment_question.strip(),
        )
