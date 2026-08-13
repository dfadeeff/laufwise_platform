"""thevea destination connector (ADR-0004 role, extended by ADR-0005).

thevea is the **destination** of a governed import. Four capabilities, all read-or-create:
`find_patient` / `create_patient` (the patient card) and `find_appointment` /
`create_appointment` (the appointment bound to it). No update/delete exists for either entity, so
the import is append-only / never-replace by construction (ADR-0004 D7, ADR-0005 D1) — thevea
*does* offer `patientAktualisieren`/`patientEntfernen`, and they are deliberately not wrapped.

thevea is a GraphQL app at `mein.thevea.de/graphql` (HotChocolate), cookie session, no 2FA.
Operations were recovered from the app's own JS bundle and verified live (2026-08-02):
  - login:   `benutzerLogin({email, password})`
  - read:    `getTermine(from, until, personenIds=[roomIds], resourceIds=[])`
  - patient: `patientenUebersicht(search)` + `patientAnlegen(PatientInput)`
  - create:  `addPatientenTermin` — replayed via its persisted query hash.
             The source ref is stored in `bemerkung` as the idempotency key.

Two shapes here are easy to get wrong and fail *silently*, so both are pinned by tests:

1. **`bemerkung` lives on the `Termin` INTERFACE**, not on `SonstigerTermin`. Selecting it inside
   an inline fragment makes patient-bound appointments invisible to `find_appointment` — which
   inverts idempotency (a duplicate on every re-run) *and* makes every verification fail.
2. **`PatientenTerminInput` is not `SonstigerTerminInput`**: the id field is `patientenId` (reads
   expose `patientId`), there is no `title`, and there is no `wiederholung`.

Timezone: BOTH systems store UTC (healthyfeet `preferred_date` ends `+00`, thevea `from/until`
end `Z`). We map the instant DIRECTLY, parsing the offset defensively and emitting `…Z` — never
treating a timestamp as naive-local.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from app.connectors.base import Appointment, Patient, PatientRef

_ADD_PATIENTEN_TERMIN_HASH = "a2c9e341f54ba198110024d378a3ce8b48a7005872c58b306c9f731d54dd5ff9"
_DEFAULT_ROOM_ID = 208413  # MA 1
_DEFAULT_DURATION_MIN = 30
# thevea requires a date of birth and validates it (not in the future, not >120 years ago), but
# the practice often omits it or types junk. ONE fixed stand-in, so such cards are findable with a
# single query — and it is NEVER treated as a match (ADR-0005 D4): letting it match would collapse
# every unknown-DOB patient sharing a surname into one card.
SENTINEL_BIRTHDATE = "1911-01-01"
_MAX_AGE_YEARS = 120
# Markers written into free text so a human can see how a record got there.
_IMPORT_MARKER = "laufwise-Import"
_UNKNOWN_DOB_MARKER = "Geburtsdatum unbekannt"
_FORCED_MARKER = "ausserhalb Arbeitszeit"
_PRAXIS = "PRAXIS"  # PatientenTerminArt: PRAXIS | HAUSBESUCH | VIDEOTHERAPIE
_SEARCH_PAGE_SIZE = 50
# The cookies that TOGETHER make a thevea session. `PHPSESSID` is the actual server-side session;
# `thevea_active_session` is the "logged in" marker. BOTH must be carried to reuse a session —
# carrying only the marker gives NichtAngemeldet ("login required") on the next request.
_SESSION_COOKIE_NAMES = ("PHPSESSID", "thevea_active_session")

_LOGIN = (
    "mutation Login($input: BenutzerLoginInput!) { "
    "benutzerLogin(input: $input) { benutzerkennung __typename } }"
)
# `id/from/until/bemerkung/mandantMitarbeiterId` are selected on the `Termin` INTERFACE, so BOTH
# patient-bound appointments and the legacy patient-less ones are returned. Narrowing these into
# `... on SonstigerTermin` (as this query once did) hides every PatientenTermin — see the module
# docstring for why that fails silently.
_GET_TERMINE = (
    "query getTermine($from: Instant!, $until: Instant!, $personenIds: [Int!]!, $resourceIds: [Int!]!) { "
    "termine(input: {from: $from, until: $until, personenIds: $personenIds, resourceIds: $resourceIds}) { "
    "__typename id from until bemerkung mandantMitarbeiterId "
    "... on SonstigerTermin { title } ... on PatientenTermin { patientId } } }"
)
_PATIENT_UEBERSICHT = (
    "query patientenUebersicht($tabellenInput: PatientUebersichtInput!) { "
    "patientUebersicht(input: $tabellenInput) { "
    "nodes { id vorname nachname geburtsdatum } pageInfo { nodesCount } } }"
)
_PATIENT_ANLEGEN = (
    "mutation patientAnlegen($input: PatientInput!) { "
    "patientAnlegen(input: $input) { id vorname nachname geburtsdatum } }"
)


def _to_utc(value: str) -> datetime:
    """Parse a timestamp (e.g. '2026-06-02 08:00:00+00' or ISO) to an aware UTC datetime.
    Naive values are assumed UTC (both systems are UTC); offsets are honoured, not ignored."""
    s = value.strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # normalize a bare 2-digit tz offset that follows a time (…T09:00:00+00 -> …+00:00),
    # without misfiring on a date's own hyphens.
    if re.search(r"T\d{2}:\d{2}(:\d{2})?[+-]\d{2}$", s):
        s = s + ":00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _to_instant(value: str, *, end_of_day: bool) -> str:
    """Normalize a window bound to a thevea `Instant` (full ISO-UTC). A bare date 'YYYY-MM-DD'
    expands to the start/end of that day; a full timestamp is just converted to UTC."""
    v = value.strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":  # date only
        return f"{v}T23:59:59.000Z" if end_of_day else f"{v}T00:00:00.000Z"
    return _iso_z(_to_utc(v))


def _birthdate(value: str | None) -> tuple[str, bool]:
    """A date of birth thevea will accept, plus whether it had to be substituted.

    Unusable (absent, unparseable, in the future, or older than thevea's 120-year limit) becomes
    the ONE fixed sentinel. Emitted at midnight UTC: Berlin is always ahead of UTC, so the calendar
    date cannot slip backwards — an end-of-day value would not be safe (ADR-0005 D4).
    """
    day = str(value or "").strip()[:10]
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return f"{SENTINEL_BIRTHDATE}T00:00:00.000Z", True
    today = datetime.now(timezone.utc).date()
    # Compared as DATES, not years: thevea's limit is "not more than 120 years ago" to the day, so
    # a year-only test would accept dates it then rejects (e.g. early 1906 in late 2026) and the
    # card creation would fail. `replace` guards the 29 February case, which has no counterpart.
    try:
        earliest = today.replace(year=today.year - _MAX_AGE_YEARS)
    except ValueError:
        earliest = today.replace(year=today.year - _MAX_AGE_YEARS, day=28)
    if parsed > today or parsed < earliest:
        return f"{SENTINEL_BIRTHDATE}T00:00:00.000Z", True
    return f"{day}T00:00:00.000Z", False


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _joined(*parts: Any) -> str:
    """Free-text note: non-empty parts joined by ' · '. Callers put the source ref LAST — it is the
    idempotency key `find_appointment` matches on."""
    return " · ".join(str(p).strip() for p in parts if p and str(p).strip().lower() != "none")


class TheveaError(Exception):
    """Any failure talking to thevea — transport, HTTP, or a GraphQL-level error."""


class TheveaAbsence(TheveaError):
    """thevea refused the write because the room is absent at that time (`ABWESENHEIT`).

    Its own type because the orchestrator must tell "this room is on holiday" (try another room,
    then force) apart from "the write failed" (ADR-0005 D6). Still a TheveaError, so existing
    handlers that only know the base class keep treating it as a failure."""


class TheveaConnector:
    """DestinationCalendar over thevea's GraphQL API. find + create only (append-only, D7)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        room_id: int = _DEFAULT_ROOM_ID,
        search_room_ids: list[int] | None = None,
        window_from: str | None = None,
        window_until: str | None = None,
        # Session reuse (avoids re-logging-in per appointment): if a warm session (the full set of
        # session cookies) is supplied, skip the login round-trip; `on_login` is called with those
        # cookies after a FRESH login so the caller can cache them for the rest of the run. A
        # 10-appointment import then does ONE login, not ten — far less load on thevea. The whole
        # cookie set is required: PHPSESSID is the real session, so a partial set fails as unauthed.
        session_cookies: dict[str, str] | None = None,
        on_login: Callable[[dict[str, str]], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,  # fail fast — a longer wait just hangs when thevea is unreachable
    ) -> None:
        self._url = base_url.rstrip("/") + "/graphql"
        self._host = httpx.URL(self._url).host
        self._on_login = on_login
        self._username = username
        self._password = password
        self._room_id = room_id  # the room this connector WRITES to
        # The rooms find_appointment SEARCHES for idempotency/verification. Idempotency is
        # room-INDEPENDENT: an appointment already imported into ANY of these rooms counts as
        # present, so a positional room reassignment between runs can't create a cross-room
        # duplicate. Defaults to just the write room when the full set isn't supplied.
        self._search_room_ids = [int(r) for r in (search_room_ids or [room_id])] or [room_id]
        # The read window for find/verify (covers the import range), as thevea Instants. A bare
        # date bound is expanded to the whole day; None falls back to a broad span.
        self._from = _to_instant(window_from, end_of_day=False) if window_from else "2020-01-01T00:00:00.000Z"
        self._until = _to_instant(window_until, end_of_day=True) if window_until else "2035-01-01T00:00:00.000Z"
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)
        self._authed = False
        if session_cookies:
            for name, value in session_cookies.items():
                self._http.cookies.set(name, value, domain=self._host)
            self._authed = True  # reuse the warm session — no login this connector

    def close(self) -> None:
        self._http.close()

    # --- transport -------------------------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(
                self._url, json=payload, headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise TheveaError(f"thevea transport error: {exc}") from exc
        if body.get("errors"):
            raise TheveaError(f"thevea graphql error: {body['errors']}")
        return body.get("data") or {}

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return self._post({"query": query, "variables": variables})

    def _persisted(self, operation: str, variables: dict[str, Any], sha256: str) -> dict[str, Any]:
        return self._post(
            {
                "operationName": operation,
                "variables": variables,
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256}},
            }
        )

    @property
    def session_cookies(self) -> dict[str, str]:
        """The full set of cookies that make up a live session (empty until authenticated)."""
        return {
            c.name: c.value
            for c in self._http.cookies.jar
            if c.name in _SESSION_COOKIE_NAMES and c.value is not None
        }

    def _ensure_auth(self) -> None:
        if not self._authed:
            self._query(_LOGIN, {"input": {"email": self._username, "password": self._password}})
            self._authed = True  # the session cookies now live on the client's jar
            cookies = self.session_cookies
            if self._on_login and cookies:
                self._on_login(cookies)  # cache the warm session for the rest of the run

    def verify(self) -> None:
        """Authenticate against thevea — the connect-time credential check. Raises TheveaError on a
        bad email/password so the connection is rejected in the connect form, not at import time."""
        self._ensure_auth()

    # --- DestinationCalendar ---------------------------------------------------------------
    def find_appointment(self, ref: str) -> Appointment | None:
        """Find a previously-imported appointment by its `HF-…` ref (stored in bemerkung).
        Searches ACROSS all configured rooms, so idempotency is room-independent."""
        self._ensure_auth()
        data = self._query(
            _GET_TERMINE,
            {
                "from": self._from,
                "until": self._until,
                "personenIds": self._search_room_ids,
                "resourceIds": [],
            },
        )
        for termin in data.get("termine") or []:
            if isinstance(termin, dict) and ref in (termin.get("bemerkung") or ""):
                return Appointment(ref=ref, start=termin.get("from", ""), raw=termin)
        return None

    def find_patient(self, patient: Patient, *, strict: bool = True) -> PatientRef | None:
        """The card for this person — but the flag decides *which question* is being asked.

        `strict=True` (the default, used to BIND an appointment): only when the name AND a real
        date of birth agree. A substituted date is "unknown", and unknown never equals unknown, so
        it never binds (ADR-0005 D4) — the caller creates a new card instead. A duplicate card is
        visible and fixable by hand; attaching one person's appointment to another's card is not.

        `strict=False` (used only to VERIFY that a card exists): a sentinel date matches a stored
        sentinel, because the question is "did a card for this person land?" rather than "is this
        certainly the same human?". Verification must be able to see the card it just wrote, and
        for an unknown-DOB patient "a card with this name and no known date" is the strongest
        state-grounded answer available.
        """
        self._ensure_auth()
        dob_iso, substituted = _birthdate(patient.geburtsdatum)
        if substituted and strict:
            return None  # no usable date of birth -> never confident enough to bind (D4)
        want = dob_iso[:10]

        data = self._query(
            _PATIENT_UEBERSICHT,
            {
                "tabellenInput": {
                    "search": patient.nachname,
                    # ZERO-based — verified live 2026-08-03. Sending 1 asks for the SECOND page,
                    # which is empty for any search returning less than a full page, so every
                    # lookup would miss and a new card would be created for every appointment.
                    "currentPage": 0,
                    # One generous page instead of pagination: a surname search in a practice of
                    # ~2 000 patients returns a handful. If a name ever exceeded this, the miss
                    # creates a duplicate card — the outcome the owner accepts — never a wrong match.
                    "pageSize": _SEARCH_PAGE_SIZE,
                    "zeigeInaktive": True,
                }
            },
        )
        for node in (data.get("patientUebersicht") or {}).get("nodes") or []:
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            if _norm(node.get("nachname")) != _norm(patient.nachname):
                continue
            if _norm(node.get("vorname")) != _norm(patient.vorname):
                continue
            got = str(node.get("geburtsdatum") or "")[:10]
            if not got or got != want:
                continue
            return PatientRef(
                id=int(node["id"]),
                vorname=str(node.get("vorname") or ""),
                nachname=str(node.get("nachname") or ""),
                geburtsdatum=got,
            )
        return None

    def create_patient(self, patient: Patient) -> PatientRef:
        """Append a patient card carrying exactly the practice's field list (ADR-0005 D5).

        Reminder flags are forced OFF: the agent must never cause thevea to email or text a
        patient. `krankenversicherung` is a required wrapper with no required content, so an empty
        object satisfies it — insurance data is not needed to create a card.
        """
        self._ensure_auth()
        geburtsdatum, substituted = _birthdate(patient.geburtsdatum)
        anschrift = {
            key: str(value).strip()
            for key, value in (
                ("strasseUndHausnummer", patient.strasse),
                ("postleitzahl", patient.plz),
                ("ort", patient.ort),
            )
            if value and str(value).strip()
        }
        kontakt = {"email": patient.email.strip()} if patient.email and patient.email.strip() else {}
        payload = {
            "vorname": patient.vorname,
            "nachname": patient.nachname,
            "geburtsdatum": geburtsdatum,
            "anschrift": anschrift,
            "kontakt": kontakt,
            "krankenversicherung": {},
            # Records where the card came from, so our own creations are findable by an exact
            # query rather than by fuzzy duplicate detection — and flags a substituted birthdate.
            "bemerkung": _joined(
                _IMPORT_MARKER,
                patient.source,
                _UNKNOWN_DOB_MARKER if substituted else None,
                patient.source_ref,
            ),
            "sichtbar": True,
            "terminErinnerungPerEmail": False,
            "terminErinnerungPerSMS": False,
        }
        created = self._query(_PATIENT_ANLEGEN, {"input": payload}).get("patientAnlegen") or {}
        if created.get("id") is None:
            raise TheveaError(f"thevea did not return a patient id: {created}")
        return PatientRef(
            id=int(created["id"]),
            vorname=str(created.get("vorname") or patient.vorname),
            nachname=str(created.get("nachname") or patient.nachname),
            geburtsdatum=str(created.get("geburtsdatum") or geburtsdatum)[:10],
        )

    def create_appointment(
        self, appt: Appointment, *, patient_id: int, force: bool = False
    ) -> None:
        """Append an appointment bound to a patient card (append-only, D7).

        `PatientenTermin` has no `title`, so the procedure name goes into `bemerkung` — with the
        source ref LAST, because that is the idempotency key `find_appointment` matches on.
        `force` sets `ignoreValidation`, which is what thevea's own UI does behind its "are you
        sure?" confirmation; it is the last rung of the placement ladder (D6) and is marked in the
        note so a forced appointment is visible in the calendar, never silent.
        """
        self._ensure_auth()
        start = _to_utc(appt.start)
        end = start + timedelta(minutes=_DEFAULT_DURATION_MIN)
        raw = appt.raw or {}
        procedure = raw.get("service_label") or appt.type or ""
        termin_input = {
            "sequenceId": 0,
            "patientenId": int(patient_id),
            "patientenTerminArt": _PRAXIS,
            "from": _iso_z(start),
            "until": _iso_z(end),
            "mandantMitarbeiterId": self._room_id,
            "kategorieId": -1,
            # Procedure, phone, then the ref LAST (the idempotency key `find_appointment` matches).
            # The phone is here rather than on the patient card because the card carries the
            # practice's agreed field list and no more (ADR-0005 D5) — and because the reason to
            # reach for it is this appointment: a patient who has to be called back about today.
            "bemerkung": _joined(
                _FORCED_MARKER if force else None, procedure, raw.get("phone"), appt.ref
            ),
            "status": None,
            "terminfarbe": "MITARBEITER",
            "resourceIds": [],
            "clientId": f"lw-{appt.ref}",
            "ignoreValidation": bool(force),
        }
        data = self._persisted(
            "addPatientenTermin",
            {"input": {"terminInput": termin_input}},
            _ADD_PATIENTEN_TERMIN_HASH,
        )
        result = (data.get("addPatientenTermin") or {}).get("validationResult") or {}
        if result.get("type") == "SUCCESS" and result.get("createdTermineIds"):
            return
        if "ABWESENHEIT" in (result.get("errorTypes") or []):
            raise TheveaAbsence(
                f"room {self._room_id} is absent at {_iso_z(start)}: {result.get('errorTypes')}"
            )
        raise TheveaError(f"thevea rejected the appointment: {result or data}")
