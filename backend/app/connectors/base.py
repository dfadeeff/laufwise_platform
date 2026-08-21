"""The calendar-connector abstraction (ADR-0004).

Both systems in a governed import are modelled as connectors so the runtime stays
domain-agnostic (PLATFORM_PLAN: runbook is data, agent is a plugin). Two capability protocols:

- `SourceCalendar` — read-only: enumerate + fetch appointments to copy (e.g. healthyfeet).
- `DestinationCalendar` — read + create ONLY (e.g. thevea), for BOTH entities it touches:
  appointments and patient cards (ADR-0005 D1). **There is deliberately no update or delete
  method** — append-only / never-replace (ADR-0004 D7) is enforced by the fact that the capability
  does not exist, not by convention. thevea does expose `patientAktualisieren`/`patientEntfernen`;
  they are deliberately not wrapped, so a run physically cannot modify or remove a patient card.

A connector talks to a real system with credentials; concrete ones (thevea GraphQL, healthyfeet
REST) live next to this file and are built via `build_connector`.
"""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class Patient:
    """The patient a source appointment belongs to, in transit to the destination's card
    (ADR-0005 D5). Deliberately exactly the practice's field list — names, date of birth, address,
    email, phone — plus where it came from, which is recorded on the card so our own creations
    stay findable by an exact query instead of fuzzy duplicate detection.

    `geburtsdatum` is `YYYY-MM-DD` or None; the destination decides what to do with an unusable
    one (thevea's range rule is thevea's quirk, so the sentinel lives in that connector).
    """

    vorname: str
    nachname: str
    geburtsdatum: str | None = None
    strasse: str | None = None
    plz: str | None = None
    ort: str | None = None
    email: str | None = None
    # Verbatim, as the source collected it. The destination owns "is this a usable number?" —
    # thevea's E.164 rule is thevea's quirk, so it normalises (or drops) in that connector.
    telefon: str | None = None
    source_ref: str = ""
    source: str = ""


@dataclass(frozen=True)
class PatientRef:
    """A patient card that exists in the destination — the handle an appointment binds to."""

    id: int
    vorname: str = ""
    nachname: str = ""
    geburtsdatum: str | None = None


def _first(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value and str(value).strip().lower() != "none":
            return str(value).strip()
    return None


# Day-first, as both source systems' German users type it: `26.01.1988`, `26-01-1988`, `1.2.1988`.
_GERMAN_DATE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")
# One composed address line -> the three fields the destination's card has, anchored on the
# 5-digit German postcode. `[,\s]*` so a line that is only "80331 München" still splits.
_ONE_LINE_ADDRESS = re.compile(r"^(?P<street>.*?)[,\s]*(?P<plz>\d{5})\s+(?P<ort>.+)$")


# Nobiliary particles: "Hans von Neumann" is one surname, not a middle name.
_SURNAME_PARTICLES = frozenset(
    {"von", "vom", "van", "zu", "zum", "zur", "de", "del", "der", "den", "di", "da", "la", "le",
     "ter", "ten"}
)


def _split_name(whole: str) -> tuple[str, str]:
    """A composed display name as `(vorname, nachname)`.

    A FALLBACK: both sources now send the two fields apart, and those always win. This runs for
    healthyfeet bookings taken before its `first_name`/`last_name` columns existed, which carry
    only the composed name.

    The LAST word is the surname there, since a multi-word first name ("Anna Maria") is far
    commoner than a multi-word surname — the exception being a particle, which belongs to the
    surname. Not cosmetic: the surname is what the destination's patient search is keyed on, so a
    wrong split misses a card the practice typed by hand and opens a duplicate (ADR-0005 D3).
    """
    parts = whole.split()
    if len(parts) < 2:
        return "", (parts[0] if parts else "")
    cut = len(parts) - 1
    while cut > 1 and parts[cut - 1].casefold() in _SURNAME_PARTICLES:
        cut -= 1
    return " ".join(parts[:cut]), " ".join(parts[cut:])


def _iso_date(value: str | None) -> str | None:
    """A source's date of birth as `YYYY-MM-DD` — day-first when it is written the German way.

    Not cosmetic: the date of birth is the only discriminator strong enough to match a patient on
    (ADR-0005 D3). An unrecognised one becomes the destination's "unknown" sentinel, which never
    matches, so every visit of the same person would open a new card. Anything this cannot read is
    passed through untouched — deciding "usable or not" is the destination's job, not ours.
    """
    if value is None:
        return None
    match = _GERMAN_DATE.match(value)
    if match is None:
        return value  # already ISO, or junk the destination will substitute
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def patient_from_appointment(appt: Appointment, source: str = "") -> Patient:
    """The patient an appointment belongs to, from whatever shape its source produced.

    Sources differ: doctolib carries a nested `patient` dict flattened alongside per-field keys
    (including the address already split), healthyfeet carries a single display name, a German
    date of birth and one composed address line. Both are normalised here rather than in either
    connector, so the destination sees one shape (ADR-0005 D5). A name with no space is treated as
    a surname — that is what the destination's search is keyed on.
    """
    raw = appt.raw or {}
    nested = raw.get("patient") if isinstance(raw.get("patient"), dict) else {}
    vorname = str(nested.get("first_name") or raw.get("first_name") or "").strip()
    nachname = str(nested.get("last_name") or raw.get("last_name") or "").strip()
    if not (vorname or nachname):
        vorname, nachname = _split_name(str(appt.patient or raw.get("name") or "").strip())
    strasse = _first(raw, "street", "strasse", "address")
    plz = _first(raw, "zip", "zipcode", "plz", "postleitzahl")
    ort = _first(raw, "city", "ort")
    if strasse and not (plz or ort):
        # Only a composed line reached us (healthyfeet). Split it, or leave it whole — never guess
        # a city out of a line with no postcode in it.
        match = _ONE_LINE_ADDRESS.match(strasse)
        if match is not None:
            strasse = match.group("street").strip(" ,") or None
            plz, ort = match.group("plz"), match.group("ort").strip()
    return Patient(
        vorname=vorname,
        nachname=nachname,
        geburtsdatum=_iso_date(_first(raw, "birth_date", "birthdate", "geburtsdatum")),
        strasse=strasse,
        plz=plz,
        ort=ort,
        email=_first(raw, "email"),
        telefon=_first(raw, "phone", "phone_number", "telefon"),
        source_ref=appt.ref,
        source=source,
    )


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

    def find_patient(self, patient: Patient, *, strict: bool = True) -> PatientRef | None:
        """The card for this person. `strict=True` answers "is this certainly the same human?"
        (used to BIND an appointment — an unknown date of birth never binds, ADR-0005 D3/D4);
        `strict=False` answers "did a card for this person land?" (used only by the
        `ensure_patient` postcondition, which must be able to see the card it just wrote)."""
        ...

    def create_patient(self, patient: Patient) -> PatientRef:
        """Append a patient card. Create-only — there is no update/delete (ADR-0005 D1)."""
        ...

    def create_appointment(
        self, appt: Appointment, *, patient_id: int, force: bool = False
    ) -> None:
        """Append an appointment bound to a patient card. `force` bypasses the destination's own
        working-hours check — the last rung of the placement ladder (ADR-0005 D6), never a default.
        Create-only: no update/delete exists (D7)."""
        ...

    def close(self) -> None: ...


def build_connector(adapter: str, base_url: str, credentials: dict[str, str], **opts: Any) -> Any:
    """Construct a connector by adapter name. Imported lazily to avoid import cycles and to keep
    httpx/connector deps out of modules that only need the protocols. `opts` are adapter-specific
    (e.g. the destination's `room_id` for the appointment being written)."""
    user, pw = credentials.get("username", ""), credentials.get("password", "")
    if adapter == "thevea":
        from app.providers.thevea import TheveaConnector

        return TheveaConnector(base_url, user, pw, **opts)
    if adapter == "healthyfeet":
        from app.providers.healthyfeet import HealthyfeetConnector

        return HealthyfeetConnector(base_url, user, pw)
    if adapter == "doctolib":
        from app.providers.doctolib import DoctolibConnector

        # A captured session / one-time email code ride in credentials (not username/password);
        # agenda_ids + window ride in opts (from the connection config / run window).
        return DoctolibConnector(
            base_url,
            user,
            pw,
            session_cookie=credentials.get("session_cookie"),
            otp_code=credentials.get("otp_code"),
            **opts,
        )
    raise ValueError(f"unknown connector adapter {adapter!r}")