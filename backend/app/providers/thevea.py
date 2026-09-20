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
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from app.connectors.base import Appointment, BusyRange, Patient, PatientRef

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
# Length bounds of thevea's own phone validator: "the number, including +country code, may hold at
# most 17 characters". The floor is ours — "+49" plus a handful of digits is the shortest thing
# that can be a real number, and a stray fragment is better dropped than stored as a phone.
_E164_MAX = 17
_E164_MIN = 8
# The candidate set is fetched by the surname's FIRST LETTER, not the surname: thevea's own search
# would never return "Müller" for "Mueller", so a spelling difference would be invisible before any
# comparison could forgive it. One letter of a ~2 000-patient practice is a few hundred rows,
# fetched once per letter per import and filtered here.
_SEARCH_PAGE_SIZE = 500
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
    "__typename id from until bemerkung status mandantMitarbeiterId "
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


# The website's own booking ref as `create_appointment` writes it into `bemerkung`
# (the site generates `HF-YYMMDD-XXXX`). It is the ONLY thing the occupancy read takes out of the
# note — no name, no procedure, no phone number ever leaves this connector for the website.
_SITE_REF = re.compile(r"HF-\d{6}-[A-Z0-9]{4}")
# Entry types that occupy a room for the booking website. An ALLOWLIST on purpose: an absence
# (holiday, sick leave, Hausbesuch) stays a manual decision on the site, and an entry type thevea
# adds later must not silently close or free the practice's online slots (ADR-0009, owner decision).
_OCCUPYING_TYPES = ("PatientenTermin", "SonstigerTermin")

# A cancelled appointment does not hold its room. thevea keeps the entry and marks it on the
# `Termin` interface (`status: "ABGESAGT"`), so without this the website would go on offering
# nothing at a time the practice has already freed — the inverse of the mirror's purpose, and the
# open question ADR-0009 left for the live account to answer. Measured on 2026-09-20: 4 of 262
# entries in the booking horizon, holding 4 places closed for nothing.
#
# A DENYLIST, unlike `_OCCUPYING_TYPES` above, and deliberately so: the two fail in opposite
# directions. An unknown *type* must not close a slot, but an unknown *status* must not free one —
# guessing that some new status means "cancelled" would hand the website a room that is actually
# taken, and double-book a patient. Anything not listed here keeps occupying.
_CANCELLED_STATUSES = ("ABGESAGT",)


def _berlin_day_bounds(day: str) -> tuple[str, str]:
    """One practice day (`YYYY-MM-DD`) as thevea Instants, from its first to its last second.

    The practice's day, not UTC's: 00:30 Berlin is still yesterday in UTC, so a UTC day would read
    one day's edge appointments and miss the other's — and it drifts by an hour between summer and
    winter time, which is exactly when a mirrored day would quietly go wrong.
    """
    start = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Berlin"))
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return _iso_z(start.astimezone(timezone.utc)), _iso_z(end.astimezone(timezone.utc))


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


def _e164(value: str | None) -> str | None:
    """A phone number as thevea's `Rufnummer` constraint wants it (E.164, `+49…`), or None.

    Every phone field of `PatientKontaktInput` is validated: the number must carry a country code
    and fit E.164's 17 characters. Both sources collect free text — healthyfeet's booking form
    accepts anything phone-shaped — so the German conventions (`0176…`, `0049…`, `+49 (0)176…`)
    are read here, in the connector whose rule this is. Anything still unrecognisable is DROPPED
    rather than sent: a refused number would fail `patientAnlegen`, so one odd phone would block
    the whole import of that appointment. Nothing is lost — the appointment's `bemerkung` carries
    the number verbatim either way.
    """
    digits = re.sub(r"[^\d+]", "", str(value or ""))
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0"):
        digits = "+49" + digits[1:]
    # "+49 (0)176…" — the German way of writing a number for both callers at once. The trunk zero
    # is not part of the international form; kept, it would be a different (wrong) number.
    if digits.startswith("+490"):
        digits = "+49" + digits[4:]
    if not digits.startswith("+") or not digits[1:].isdigit():
        return None
    return digits if _E164_MIN <= len(digits) <= _E164_MAX else None


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _fold(value: Any) -> str:
    """A name reduced to what two spellings of the SAME name share.

    German transliterates its umlauts when a keyboard or a form cannot carry them, so `Müller` and
    `Mueller` are one name written two ways — not two people. Accents are stripped for the same
    reason, and punctuation dropped so `Zeller-Klaus`, `Zeller Klaus` and `ZellerKlaus` agree.
    """
    text = str(value or "").strip().casefold()
    for umlaut, plain in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, plain)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))


_UMLAUT_PAIRS = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def _spelling_variants(name: str) -> list[str]:
    """The same surname, spelled the ways thevea might be holding it.

    thevea's search is case-insensitive but does NOT fold umlauts — measured on the live account
    2026-09-20: `Müller` finds 32 cards, `Mueller` finds 1, and they are different sets; `Weiß`
    finds 6 where `Weiss` finds 2. So a card written one way is invisible to a search written the
    other, and the import would create a second card for a patient it already has.

    Both directions are produced because either side can be the transliterated one: the practice
    may have typed `Mueller` into thevea while the website sends `Müller`, or the reverse.

    The reverse direction guesses, and sometimes wrongly — `Bauer` contains `ue`, so `baür` is
    offered too. That costs one cached query returning nothing. The other way round costs a
    duplicate patient card, so the trade is not close.
    """
    base = str(name or "").strip()
    if not base:
        return []
    variants = [base]
    # `.lower()`, never `.casefold()`: casefold expands ß to ss by itself, which silently eats the
    # ß -> ss direction below and leaves only the wrong one. Caught by a test, not by reading.
    lowered = base.lower()
    folded = lowered
    for umlaut, digraph in _UMLAUT_PAIRS:
        folded = folded.replace(umlaut, digraph)
    unfolded = lowered
    for umlaut, digraph in _UMLAUT_PAIRS:
        unfolded = unfolded.replace(digraph, umlaut)
    for candidate in (folded, unfolded):
        if candidate and candidate != lowered and candidate not in variants:
            variants.append(candidate)
    return variants


def _one_edit_apart(a: str, b: str) -> bool:
    """True when ONE inserted, deleted or replaced character turns `a` into `b`."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    i = j = 0
    spent = False
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        if spent:
            return False
        spent = True
        j += 1                      # skip the extra character in the longer name
        if len(short) == len(long):
            i += 1                  # same length -> it was a substitution
    return True


def _is_typo_of(a: str, b: str) -> bool:
    """One typo apart — at any length.

    A length floor was considered as a guard against twins, who share a surname and a date of birth
    and could in principle differ by one letter (`Anna`/`Anne`). The owner rejected it (2026-08-13):
    twins are not named that alike in practice, and the floor was costing real matches on short
    names. The remaining guard is the one that matters — only ONE of the two names may differ.
    """
    return _one_edit_apart(a, b)


def _same_person_name(node: dict[str, Any], patient: Patient) -> bool:
    """Whether these two names are the same human's, allowing ONE near-miss but never two.

    Both names differing at once is not a typo — it is a different person, and the practice would
    rather have a duplicate card to clean up than an appointment filed under someone else
    (ADR-0005 D3).
    """
    first_here, first_there = _fold(node.get("vorname")), _fold(patient.vorname)
    last_here, last_there = _fold(node.get("nachname")), _fold(patient.nachname)
    if first_here == first_there and last_here == last_there:
        return True
    if first_here == first_there and _is_typo_of(last_here, last_there):
        return True
    return last_here == last_there and _is_typo_of(first_here, first_there)


def _joined(*parts: Any) -> str:
    """Free-text note: non-empty parts joined by ' · '. Callers put the source ref LAST — it is the
    idempotency key `find_appointment` matches on."""
    return " · ".join(str(p).strip() for p in parts if p and str(p).strip().lower() != "none")


# How often a READ is re-asked when the request never completed, and how long to wait between
# attempts. Small on purpose: this exists to survive the practice calendar dropping a connection,
# not to sit through an outage — the governed loop blocking is still the right answer to one.
_READ_RETRY_BACKOFF_S = (1.0, 3.0)


class TheveaError(Exception):
    """Any failure talking to thevea — transport, HTTP, or a GraphQL-level error."""


class TheveaUnreachable(TheveaError):
    """The request never completed — no answer came back at all.

    Kept apart from its parent because the two say different things about what may be done next.
    A GraphQL error means thevea answered and declined; a dropped connection or a read timeout
    means the question never got through, and asking a READ again is therefore safe. It stays a
    `TheveaError`, so every existing handler still treats it as the blocking failure it is once
    the retries are spent.
    """


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
        # Patient pages by surname initial, filled lazily and kept for this connector.
        self._patient_pages: dict[str, list[dict[str, Any]]] = {}
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
        except httpx.TransportError as exc:
            # Connection reset, read timeout, DNS — the request did not complete.
            raise TheveaUnreachable(f"thevea transport error: {exc}") from exc
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            # A status code or an unparseable body: thevea DID answer. Not retryable.
            raise TheveaError(f"thevea transport error: {exc}") from exc
        if body.get("errors"):
            raise TheveaError(f"thevea graphql error: {body['errors']}")
        return body.get("data") or {}

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Send a GraphQL document, re-asking a READ that never got through.

        The retry is allowed ONLY for a document whose operation is `query`, and the reason is
        the write, not the read: a dropped connection is ambiguous about whether the write landed,
        so re-sending `patientAnlegen` or `addPatientenTermin` could append a second patient card
        or a second appointment — the one thing append-only cannot take back (ADR-0004 D7). A read
        re-asked is just the same question.

        The test is the document itself rather than a flag at the call site, so a mutation added
        later is excluded by default instead of by whoever remembers. `_persisted` never comes
        through here, and it carries the appointment mutation.

        Governance is untouched: when the attempts are spent this raises exactly as before, the
        provider turns it into `StateUnavailable`, and the engine BLOCKs. All this buys is that a
        single dropped connection stops costing an appointment.
        """
        if not query.lstrip().lower().startswith("query"):
            return self._post({"query": query, "variables": variables})
        for pause in (*_READ_RETRY_BACKOFF_S, None):
            try:
                return self._post({"query": query, "variables": variables})
            except TheveaUnreachable:
                if pause is None:
                    raise
                time.sleep(pause)
        raise AssertionError("unreachable")  # pragma: no cover

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

    # --- OccupancySource (ADR-0009) ---------------------------------------------------------
    def list_busy(self, day: str, room_ids: list[int]) -> list[BusyRange]:
        """Which of `room_ids` are taken on `day`, as times only — the reverse direction's read.

        This is a READ capability on its own protocol: a mirror run is handed this connector as an
        `OccupancySource`, so it has no way to write an appointment here (ADR-0004 D7 untouched).
        """
        self._ensure_auth()
        rooms = [int(r) for r in (room_ids or self._search_room_ids)]
        day_from, day_until = _berlin_day_bounds(day)
        data = self._query(
            _GET_TERMINE,
            {"from": day_from, "until": day_until, "personenIds": rooms, "resourceIds": []},
        )
        busy: list[BusyRange] = []
        for termin in data.get("termine") or []:
            if not isinstance(termin, dict) or termin.get("__typename") not in _OCCUPYING_TYPES:
                continue
            if str(termin.get("status") or "").upper() in _CANCELLED_STATUSES:
                continue  # cancelled in the practice — the room is free again
            room = termin.get("mandantMitarbeiterId")
            start, until = termin.get("from"), termin.get("until")
            if room is None or int(room) not in rooms or not start or not until:
                continue
            found = _SITE_REF.search(termin.get("bemerkung") or "")
            busy.append(
                BusyRange(
                    room=str(int(room)),
                    start=_iso_z(_to_utc(str(start))),
                    end=_iso_z(_to_utc(str(until))),
                    site_ref=found.group(0) if found else None,
                )
            )
        return busy

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

        for node in self._match_candidates(patient.nachname):
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            if not _same_person_name(node, patient):
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

    # --- reads the voice agent needs (see providers/thevea_calendar.py) --------------------

    def termine_between(
        self, start: datetime, until: datetime, *, room_ids: list[int]
    ) -> list[dict[str, Any]]:
        """Every appointment in a window across the given rooms — the raw nodes, unfiltered.

        The import path only ever asks "is this one ref present?"; a caller on the phone asks what
        the whole week looks like. Same query, different question, so it is exposed rather than
        reimplemented: availability is derived by SUBTRACTING these from the practice's grid.
        """
        self._ensure_auth()
        data = self._query(
            _GET_TERMINE,
            {
                "from": _iso_z(start.astimezone(timezone.utc)),
                "until": _iso_z(until.astimezone(timezone.utc)),
                "personenIds": [int(r) for r in room_ids],
                "resourceIds": [],
            },
        )
        return [t for t in (data.get("termine") or []) if isinstance(t, dict)]

    def match_candidates(self, nachname: str) -> list[dict[str, Any]]:
        """Patient nodes worth comparing against a surname. Public so the voice calendar can ask
        "how many match?" — a question `find_patient` cannot answer, because it returns one."""
        return self._match_candidates(nachname)

    def create_appointment_in_room(
        self, appt: Appointment, *, patient_id: int, room_id: int
    ) -> None:
        """Append into a NAMED room rather than this connector's single configured one.

        An import writes everything into one room; a practice has three equivalent calendars and
        the caller's slot decides which. Rather than build a connector per room, the room is an
        argument here — the write itself is unchanged, including having no `force`.
        """
        original, self._room_id = self._room_id, int(room_id)
        try:
            self.create_appointment(appt, patient_id=patient_id, force=False)
        finally:
            self._room_id = original

    def _forget_patient_searches(self) -> None:
        """Drop the cached pages after a write, so a verification read sees what we just wrote.

        All of them rather than the one term: thevea's own search matches on more than the exact
        string we asked for (the surname-initial paging, the typo tolerance), so a new card can
        legitimately appear in a page cached under a different term.
        """
        self._patient_pages.clear()

    def _search_patients(self, term: str) -> list[dict[str, Any]]:
        """One `patientUebersicht` page for a search term, cached for this connector."""
        if term not in self._patient_pages:
            data = self._query(
                _PATIENT_UEBERSICHT,
                {
                    "tabellenInput": {
                        "search": term,
                        # ZERO-based — verified live 2026-08-03. Sending 1 asks for the SECOND
                        # page, which is empty for any search returning less than a full page, so
                        # every lookup would miss and a new card would be created every time.
                        "currentPage": 0,
                        # One generous page instead of pagination. If a term ever exceeded it, the
                        # miss creates a duplicate card — the outcome the owner accepts — and never
                        # a wrong match.
                        "pageSize": _SEARCH_PAGE_SIZE,
                        "zeigeInaktive": True,
                    }
                },
            )
            nodes = (data.get("patientUebersicht") or {}).get("nodes") or []
            self._patient_pages[term] = [n for n in nodes if isinstance(n, dict)]
        return self._patient_pages[term]

    def _match_candidates(self, nachname: str) -> list[dict[str, Any]]:
        """Cards worth comparing against: the surname as written, plus how it would look
        transliterated the other way (`_spelling_variants`).

        This used to search the surname's FIRST LETTER as the safety net for a spelling thevea
        would never match. Measured on the live account 2026-09-20, that net has a hole the size
        of the practice: a single letter matches up to 2331 cards and `_SEARCH_PAGE_SIZE` returns
        500 of them, so the card being looked for is usually not in the answer — and the miss is
        silent, ending in a duplicate patient card. Three targeted terms beat one arbitrary fifth
        of the register.

        Every term is cached for the connector, so an import asks at most these few queries per
        surname however many appointments that patient has.
        """
        seen: set[int] = set()
        merged: list[dict[str, Any]] = []
        for term in _spelling_variants(nachname):
            if not term:
                continue
            for node in self._search_patients(term):
                key = node.get("id")
                if isinstance(key, int) and key not in seen:
                    seen.add(key)
                    merged.append(node)
        return merged

    def create_patient(self, patient: Patient) -> PatientRef:
        """Append a patient card carrying exactly the practice's field list (ADR-0005 D5, amended
        2026-08-21: the phone is on the card as well as on the appointment).

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
        # `PatientKontaktInput`: email, telefonnummer, handynummer, weitereTelefonnummern — all
        # optional. Both sources hand over ONE number without saying whether it is a landline or a
        # mobile, so it goes into the general `telefonnummer`; guessing from the prefix would put a
        # wrong claim on the card.
        kontakt: dict[str, str] = {}
        if patient.email and patient.email.strip():
            kontakt["email"] = patient.email.strip()
        telefonnummer = _e164(patient.telefon)
        if telefonnummer:
            kontakt["telefonnummer"] = telefonnummer
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
        # The search cache now contains a page that predates this card, and the very next thing a
        # booking does is re-read it to verify the card exists (`patient_card_confirmed`). Left
        # alone, that check answers from the stale page, the postcondition rejects a booking that
        # actually happened, and the caller is told it could not be confirmed — while the card
        # sits in thevea, ready to be duplicated by their second attempt. Observed against the
        # live practice calendar, 21 September 2026.
        self._forget_patient_searches()
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
            # The phone is ALSO on the patient card now (ADR-0005 D5, amended 2026-08-21), and it
            # stays here for three reasons: a card is written once and never updated (D1), so a
            # returning patient's existing card would otherwise never receive a number; this note
            # is what the day view shows without opening the card; and it keeps a number that
            # thevea's own validator refuses, which `_e164` drops from the card.
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
