"""thevea destination connector (ADR-0004 role) — real operations captured 2026-07-09.

thevea is the **destination** of a governed import: `find_appointment` (read, for idempotency +
verification) and `create_appointment` — and nothing else. No update/delete exists, so the import
is append-only / never-replace by construction (ADR-0004 D7).

thevea is a GraphQL app at `mein.thevea.de/graphql` (HotChocolate), cookie session
(`thevea_active_session`), no 2FA. Operations were recovered from the app's own JS + one DevTools
capture (see memory `thevea-api-shape`):
  - login:  `benutzerLogin({email, password})`
  - read:   `getTermine(from, until, personenIds=[roomId], resourceIds=[])`
  - create: `addSonstigerTermin` (a patient-less room appointment) — replayed via its persisted
            query hash. The source `HF-…` ref is stored in `bemerkung` as the idempotency key.

Timezone: BOTH systems store UTC (healthyfeet `preferred_date` ends `+00`, thevea `from/until`
end `Z`). We map the instant DIRECTLY, parsing the offset defensively and emitting `…Z` — never
treating a timestamp as naive-local.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.connectors.base import Appointment

_ADD_SONSTIGER_TERMIN_HASH = "974379b6ac0f08d1984fe2920d896e9132fff2be96766f759c53303bd4565aad"
_DEFAULT_ROOM_ID = 208413  # MA 1
_DEFAULT_DURATION_MIN = 30

_LOGIN = (
    "mutation Login($input: BenutzerLoginInput!) { "
    "benutzerLogin(input: $input) { benutzerkennung __typename } }"
)
_GET_TERMINE = (
    "query getTermine($from: Instant!, $until: Instant!, $personenIds: [Int!]!, $resourceIds: [Int!]!) { "
    "termine(input: {from: $from, until: $until, personenIds: $personenIds, resourceIds: $resourceIds}) { "
    "__typename ... on SonstigerTermin { id from until title bemerkung mandantMitarbeiterId } } }"
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


class TheveaError(Exception):
    """Any failure talking to thevea — transport, HTTP, or a GraphQL-level error."""


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
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/graphql"
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

    def _ensure_auth(self) -> None:
        if not self._authed:
            self._query(_LOGIN, {"input": {"email": self._username, "password": self._password}})
            self._authed = True  # the session cookie now lives on the client's jar

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

    def create_appointment(self, appt: Appointment) -> None:
        """Append a patient-less room appointment carrying the source ref (append-only, D7)."""
        self._ensure_auth()
        start = _to_utc(appt.start)
        end = start + timedelta(minutes=_DEFAULT_DURATION_MIN)
        raw = appt.raw or {}
        procedure = raw.get("service_label") or appt.type or ""
        # Title: patient name first, then the procedure — "Vorname Nachname · Eingewachsen".
        title = f"{appt.patient} · {procedure}".strip(" ·") or appt.ref
        # Bemerkung: the practice's contact details in a FIXED order — phone, date of birth,
        # email, address — with empty fields dropped (no blank " · " gaps). The HF ref stays
        # LAST: it is the idempotency key (find_appointment matches on it) and reads fine at the
        # end of the note.
        details = [raw.get("phone"), raw.get("birth_date"), raw.get("email"), raw.get("address")]
        parts = [str(d).strip() for d in details if d and str(d).strip().lower() != "none"]
        bemerkung = " · ".join([*parts, appt.ref])
        termin_input = {
            "sequenceId": 0,
            "title": title,
            "from": _iso_z(start),
            "until": _iso_z(end),
            "mandantMitarbeiterId": self._room_id,
            "kategorieId": -1,
            "bemerkung": bemerkung,
            "status": None,
            "wiederholung": None,
            "terminfarbe": "MITARBEITER",
            "resourceIds": [],
            "clientId": f"hf-{appt.ref}",
            "ignoreValidation": False,
        }
        data = self._persisted(
            "addSonstigerTermin",
            {"input": {"terminInput": termin_input}},
            _ADD_SONSTIGER_TERMIN_HASH,
        )
        result = (data.get("addSonstigerTermin") or {}).get("validationResult") or {}
        if result.get("type") != "SUCCESS" or not result.get("createdTermineIds"):
            raise TheveaError(f"thevea rejected the appointment: {result or data}")
