"""doctolib source connector (ADR-0004) — a SECOND source for the same thevea destination.

The SOURCE of a governed import (read-only): `list_appointments` (the import work-list) and
`get_appointment` (re-grounds each governed run). It never writes anywhere. Reads are mapped into
the SAME `Appointment` shape thevea already consumes, so the thevea destination + the
`calendar_import` template are untouched — this is a new plugin on the existing `source` seam,
not an engine change.

Recon (memory `doctolib-pro-recon`, captured 2026-07-17/18) — two very different halves:

READ (verified live 2026-07-18 from Railway's IP; exercised by tests below):
  GET  https://pro.doctolib.de/calendar_display/appointments?agenda_ids=<id>&start_date=<Y-m-d H:M:S>
       &end_date=<...>&view=day&include_patients=true
  Called ONCE PER agenda_id (an account has several — two practitioners here). Response is
  {"data": [ {id, start_date, end_date, status, visit_motive_id, patient:{...}}, ... ]}. `id` is
  an opaque Rails-signed token, stable per appointment -> our idempotency `ref`. `start_date`
  carries a local offset (+02:00), NOT UTC — thevea's `_to_utc` already honours offsets, so no
  new date handling is needed. The nested `patient` is flattened into the flat `raw` keys thevea's
  `create_appointment` reads (phone/birth_date/email/address), so thevea's write side is unchanged.

AUTH (NOT fully captured — CAPTURE-DEPENDENT, see the marked block):
  Login is Keycloak OIDC at auth.doctolib.de, behind Cloudflare, with an email 2FA code on a NEW
  device only (a long-lived `pin_login` cookie suppresses it thereafter). Two supported modes:
    - session replay (reliable): construct with a captured `_doctolib_session` cookie and reads
      just work — this is the path the tests use and the path a stored connection should prefer.
    - password login (best-effort): the standard Keycloak form flow, marked below. Unverified end
      to end; a genuine new-device login (email code) still needs one live capture to confirm.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.connectors.base import Appointment


class DoctolibError(Exception):
    """Any failure reading (or authenticating to) doctolib — transport, HTTP, or auth."""


class DoctolibSessionExpired(DoctolibError):
    """The stored session is no longer accepted (401/403).

    Its own type, and its own message, because the remedy is specific and human: reconnect the
    account so a fresh session is captured. Reported as a bare `401 Unauthorized for url
    .../api/accounts` it reads like a doctolib outage, and the operator waits for a system that is
    working fine. Still a DoctolibError, so the state providers keep turning it into a BLOCK
    rather than a false "no appointments" (ADR-0004: fail closed).
    """


def _read_failed(exc: Exception, what: str) -> DoctolibError:
    """The error to raise for a failed authenticated read — session-expired told apart from the
    rest. Only 401/403 mean "this session is dead": sending someone to re-authenticate during an
    outage wastes the one action that would actually help later."""
    response = getattr(exc, "response", None)
    if response is not None and response.status_code in (401, 403):
        return DoctolibSessionExpired(
            f"doctolib rejected the stored session (HTTP {response.status_code}) — it has expired. "
            "Reconnect the doctolib account in the studio, then redeploy the instance so it uses "
            "the new connection."
        )
    return DoctolibError(f"{what}: {exc}")


# Verified live (2026-07-18) from Railway's IP: the agenda-list endpoint is under
# /calendar_display/appointments (the bare /appointments 400s). One GET per agenda_id.
_APPOINTMENTS_PATH = "/calendar_display/appointments"
# The account bootstrap: `data["agendas"]` is the list of this account's agendas ({id, name, ...}).
# Used to AUTO-DISCOVER agenda ids so the user never has to supply them (verified 2026-07-19).
_ACCOUNTS_PATH = "/api/accounts"
# get_appointment re-grounds a ref we already know is in the import window; when no explicit
# window is supplied it scans a broad forward range rather than all-time (the API is date-bounded,
# unlike healthyfeet's whole-calendar page).
_DEFAULT_SCAN_DAYS = 400


def _split_ids(value: Any) -> list[str]:
    """Agenda ids as a list of strings, from a list or a comma-separated string (config stores
    strings). Empty/blank entries dropped."""
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [p.strip() for p in str(value or "").split(",")]
    return [i for i in items if i]


def _bounds(window: dict[str, Any] | None, fallback_from: str | None, fallback_to: str | None) -> tuple[str, str]:
    """Doctolib's start_date/end_date as 'YYYY-MM-DD HH:MM:SS' day bounds. Prefers the run's
    window; falls back to the connector's stored window; finally to a broad forward range."""
    window = window or {}
    lo = window.get("from") or fallback_from
    hi = window.get("to") or fallback_to
    if not lo:
        lo = datetime.now(timezone.utc).date().isoformat()
    if not hi:
        hi = (datetime.now(timezone.utc) + timedelta(days=_DEFAULT_SCAN_DAYS)).date().isoformat()
    return f"{lo} 00:00:00", f"{hi} 23:59:59"


def _compose_address(patient: dict[str, Any]) -> str:
    """One address string from doctolib's split fields — 'street, zip city', empties dropped."""
    street = str(patient.get("address") or "").strip()
    locality = " ".join(
        p for p in (str(patient.get("zipcode") or "").strip(), str(patient.get("city") or "").strip()) if p
    )
    return ", ".join(p for p in (street, locality) if p)


def _parse_appointment(row: dict[str, Any]) -> Appointment:
    """Map one doctolib appointment row to an Appointment, flattening `patient` into the flat
    `raw` keys the destination reads. `status` is preserved so the orchestrator's confirmed-only
    safety filter works unchanged.

    The address is carried BOTH ways: `address` as one composed line, and `street`/`zip`/`city`
    separately — thevea's patient card has three distinct fields (ADR-0005 D5), and re-splitting a
    composed line would be guesswork."""
    patient = row.get("patient") or {}
    name = " ".join(
        p for p in (str(patient.get("first_name") or "").strip(), str(patient.get("last_name") or "").strip()) if p
    )
    doctolib_id = str(row.get("id", ""))
    raw = {
        **row,
        "status": row.get("status"),
        "phone": patient.get("phone_number"),
        "birth_date": patient.get("birthdate"),
        "email": patient.get("email"),
        "address": _compose_address(patient),
        "first_name": patient.get("first_name"),
        "last_name": patient.get("last_name"),
        "street": patient.get("address"),
        "zip": patient.get("zipcode"),
        "city": patient.get("city"),
        # No human-readable procedure label is present in this payload (only visit_motive_id); a
        # visit_motives lookup could resolve one later. Left empty rather than showing a bare id.
        "service_label": None,
        "doctolib_id": doctolib_id,  # the raw opaque token, kept for reference/debugging
    }
    return Appointment(
        ref=_ref(doctolib_id),
        start=str(row.get("start_date", "")),
        end=row.get("end_date"),
        type=None,
        patient=name or None,
        raw=raw,
    )


def _ref(doctolib_id: str) -> str:
    """A SHORT, stable idempotency key from doctolib's ~160-char opaque appointment token.

    The raw token can't be the ref: it's stored in thevea's `bemerkung` as the dedup key and
    re-read to verify the write, but a 162-char value truncates there — the write "succeeds" yet
    verification can't find it (proven live; a `DL-<hash>` key round-trips and verifies). The
    connector never needs the original token — reads are by date range, not by id — so a stable
    hash is sufficient everywhere the ref is used (source grounding + destination idempotency)."""
    return "DL-" + hashlib.sha1(doctolib_id.encode()).hexdigest()[:16] if doctolib_id else ""


class DoctolibConnector:
    """SourceCalendar over doctolib Pro's agenda API. Read-only. Reads across all configured
    agenda_ids and merges them into one work-list."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        agenda_ids: Any = None,
        session_cookie: str | None = None,
        otp_code: str | None = None,
        window_from: str | None = None,
        window_until: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._agenda_ids = _split_ids(agenda_ids)
        self._otp_code = otp_code
        self._window_from = window_from
        self._window_until = window_until
        # A browser-like UA is required to get past Cloudflare with a replayed session.
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        self._http = httpx.Client(
            timeout=timeout, transport=transport, follow_redirects=True, headers=headers
        )
        self._authed = False
        if session_cookie:
            # Replay path: the connection stored a captured session — no login round-trip.
            self._http.cookies.set("_doctolib_session", session_cookie, domain="pro.doctolib.de")
            self._authed = True

    def close(self) -> None:
        self._http.close()

    # --- auth ------------------------------------------------------------------------------
    def verify(self) -> None:
        """Connect-time check: authenticate and prove one authenticated read. Raises DoctolibError
        on bad credentials / an unsatisfied 2FA challenge so the connection is rejected up front.

        The read is not optional. A replayed session that no longer authenticates passes every
        check that does not actually call doctolib, and the failure then surfaces as a 401 on the
        first import — long after the operator was told the account connected. With no pinned
        agendas, `_agendas()` IS that read (the discovery call an import starts with).
        """
        self._ensure_auth()
        agendas = self._agendas()
        if agendas:
            self._get_appointments(agendas[0], _bounds(None, self._window_from, self._window_until))

    def _ensure_auth(self) -> None:
        if not self._authed:
            self._login()
            self._authed = True

    # ==========================================================================================
    # CAPTURE-DEPENDENT — the Keycloak OIDC login. Unverified end to end (see module docstring).
    # The reliable path is session replay (session_cookie); this is the best-effort automation of
    # username/password so a stored connection can refresh its own session.
    # ------------------------------------------------------------------------------------------
    def _login(self) -> None:
        """Standard Keycloak login-form flow: GET the signin page (redirects to the Keycloak
        realm), submit username+password to the form's one-time action URL, follow the redirect
        back to doctolib which sets `_doctolib_session`. If doctolib returns the email-code
        challenge, submit `otp_code` when supplied, else raise so the UI can ask for it."""
        try:
            page = self._http.get(f"{self._base}/")
            action, fields = _parse_login_form(page.text)
            if not action:
                raise DoctolibError("doctolib login form not found (login flow may have changed)")
            fields.update(username=self._username, password=self._password)
            resp = self._http.post(action, data=fields)
            if _is_otp_challenge(resp.text):
                if not self._otp_code:
                    raise DoctolibError("doctolib sent an email code — provide it to finish connecting")
                otp_action, otp_fields = _parse_login_form(resp.text)
                otp_fields["code"] = self._otp_code  # Keycloak OTP field name — CONFIRM on capture
                resp = self._http.post(otp_action or action, data=otp_fields)
        except httpx.HTTPError as exc:
            raise DoctolibError(f"doctolib login transport error: {exc}") from exc
        if "_doctolib_session" not in self._http.cookies:
            raise DoctolibError("doctolib login did not establish a session (bad credentials?)")

    # ==========================================================================================

    # --- SourceCalendar --------------------------------------------------------------------
    def _discover_agendas(self) -> list[str]:
        """The account's agenda ids, read from /api/accounts — so the user never types opaque ids.
        Excludes template agendas; keeps everything else (practitioner, resource, equipment) and
        relies on dedup-by-ref below, since the same appointment can surface in more than one."""
        try:
            resp = self._http.get(self._base + _ACCOUNTS_PATH)
            resp.raise_for_status()
            agendas = resp.json().get("agendas") or []
        except (httpx.HTTPError, ValueError) as exc:
            raise _read_failed(exc, "doctolib account lookup failed") from exc
        return [str(a["id"]) for a in agendas if isinstance(a, dict) and a.get("id") and not a.get("is_template")]

    def _agendas(self) -> list[str]:
        """Configured agenda ids if the connection pinned any; otherwise auto-discovered (cached)."""
        if not self._agenda_ids:
            self._agenda_ids = self._discover_agendas()
        return self._agenda_ids

    def _get_appointments(self, agenda_id: str, bounds: tuple[str, str]) -> list[dict[str, Any]]:
        start, end = bounds
        try:
            resp = self._http.get(
                self._base + _APPOINTMENTS_PATH,
                params={
                    "agenda_ids": agenda_id,
                    "start_date": start,
                    "end_date": end,
                    "view": "day",
                    "include_patients": "true",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _read_failed(exc, "doctolib agenda API error") from exc
        return [r for r in (body.get("data") or []) if isinstance(r, dict) and r.get("id")]

    def list_appointments(self, window: dict[str, Any]) -> list[Appointment]:
        """All appointments across the account's agendas for the window (read-only). Agendas are
        auto-discovered when the connection didn't pin any; results are deduped by appointment id,
        since one appointment can appear in more than one agenda (e.g. a resource + its equipment)."""
        self._ensure_auth()
        bounds = _bounds(window, self._window_from, self._window_until)
        appts: list[Appointment] = []
        seen: set[str] = set()
        for agenda_id in self._agendas():
            for row in self._get_appointments(agenda_id, bounds):
                appt = _parse_appointment(row)
                if appt.ref and appt.ref not in seen:
                    seen.add(appt.ref)
                    appts.append(appt)
        return appts

    def get_appointment(self, ref: str) -> Appointment | None:
        """One appointment by its opaque id — scans the configured window (no per-id endpoint is
        wired). Re-grounds each governed run against live state."""
        for appt in self.list_appointments({}):
            if appt.ref == ref:
                return appt
        return None


# --- capture-dependent HTML helpers (Keycloak login form) -------------------------------------
_FORM_ACTION_RE = re.compile(r'<form[^>]*\baction="([^"]+)"', re.I)
_HIDDEN_INPUT_RE = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)
_NAME_RE = re.compile(r'\bname="([^"]+)"', re.I)
_VALUE_RE = re.compile(r'\bvalue="([^"]*)"', re.I)


def _parse_login_form(html: str) -> tuple[str | None, dict[str, str]]:
    """Pull the form action + hidden fields out of a Keycloak login page so the one-time tokens
    (session_code/execution/tab_id) ride along with the credential POST."""
    m = _FORM_ACTION_RE.search(html or "")
    action = m.group(1).replace("&amp;", "&") if m else None
    fields: dict[str, str] = {}
    for tag in _HIDDEN_INPUT_RE.findall(html or ""):
        name = _NAME_RE.search(tag)
        if name:
            value = _VALUE_RE.search(tag)
            fields[name.group(1)] = value.group(1) if value else ""
    return action, fields


def _is_otp_challenge(html: str) -> bool:
    """Heuristic: the email-code page. Field/copy names to be confirmed against a real capture."""
    text = (html or "").lower()
    return "code" in text and ("e-mail" in text or "email" in text or "bestätigungscode" in text)
