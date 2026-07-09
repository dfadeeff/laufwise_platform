"""healthyfeet source connector (ADR-0004).

The SOURCE of a governed import: a REST admin API at
`https://www.healthyfeet-podologie.de/api/admin/`, accessed with preset credentials. Read-only —
`list_appointments` (the import work-list) and `get_appointment` (re-grounds each governed run).
It never writes anywhere.

The transport + error->SourceError mapping are complete and tested against a mock transport. The
auth call and the exact appointment endpoints/JSON shape are the capture-dependent fill-ins
(the source's auth is not yet known — token? session? basic?), isolated in the marked block.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

import httpx

from app.connectors.base import Appointment

# ==========================================================================================
# CAPTURE-DEPENDENT — mostly resolved by recon; the JSON shape is the remaining fill-in.
# ------------------------------------------------------------------------------------------
# Recon (2026-07-09): the admin API is HTTP Basic auth (confirmed via WWW-Authenticate), and the
# two live routes are /api/admin/calendar and /api/admin/bookings. `healthyfeet_source_spike.py`
# reveals which holds the appointments and their JSON shape — map that into `_parse_appointment`.
LIST_PATH = "calendar"           # GET /api/admin/calendar  (alt: "bookings")
GET_PATH = "bookings/{ref}"      # GET by id — confirm the exact path from the capture


def _auth_headers(username: str, password: str, http: httpx.Client, base: str) -> dict[str, str]:
    """HTTP Basic auth — confirmed by recon (WWW-Authenticate: Basic realm=\"Healthy Feet Admin\").
    Credentials ride on every request; there is no separate login/session step."""
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _parse_appointment(row: dict[str, Any]) -> Appointment:
    """Map one source booking dict (from the calendar page's `data-booking` JSON) to an
    Appointment. `ref` is the stable HF-… id, `preferred_date` is the slot."""
    return Appointment(
        ref=str(row.get("ref", "")),
        start=str(row.get("preferred_date", "")),
        end=None,
        type=row.get("service"),
        patient=row.get("name"),
        raw=row,
    )


# Each booking is embedded in the calendar HTML as data-booking="{…HTML-escaped JSON…}".
_BOOKING_RE = re.compile(r'data-booking="([^"]*)"')


def _bookings_from_html(html_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in _BOOKING_RE.finditer(html_text):
        try:
            rows.append(json.loads(unescape(match.group(1))))
        except ValueError:
            continue
    return rows


# ==========================================================================================


def _truncate(obj: Any, list_cap: int = 2, depth: int = 0) -> Any:
    """Shrink a JSON value to show its STRUCTURE (field names) without dumping every record —
    lists keep the first `list_cap` items, long strings are clipped."""
    if depth > 6:
        return "…"
    if isinstance(obj, list):
        head = [_truncate(x, list_cap, depth + 1) for x in obj[:list_cap]]
        return head + ([f"…(+{len(obj) - list_cap} more)"] if len(obj) > list_cap else [])
    if isinstance(obj, dict):
        return {k: _truncate(v, list_cap, depth + 1) for k, v in obj.items()}
    if isinstance(obj, str) and len(obj) > 300:
        return obj[:300] + "…"
    return obj


class SourceError(Exception):
    """Any failure reading the source admin API."""


class HealthyfeetConnector:
    """SourceCalendar over the healthyfeet REST admin API. Read-only."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._http = httpx.Client(
            base_url=self._base, timeout=timeout, transport=transport, follow_redirects=True
        )
        self._headers = _auth_headers(username, password, self._http, self._base)

    def close(self) -> None:
        self._http.close()

    def _get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        try:
            resp = self._http.get(path, params=params, headers=self._headers)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise SourceError(f"healthyfeet admin API error: {exc}") from exc

    def raw_calendar(self, window: dict[str, Any] | None = None) -> str:
        """The raw calendar HTML — read-only. The bookings are embedded as data-booking JSON."""
        return self._get_text(LIST_PATH)

    def probe(self, paths: tuple[str, ...] = ("bookings", "calendar")) -> list[dict[str, Any]]:
        """Read-only capture diagnostic: find how the admin page carries appointment data —
        embedded JSON (`__NEXT_DATA__` / JSON scripts), a referenced `/api/...` data endpoint, or
        server-rendered HTML — and return whichever it is (JSON structure-truncated, or a raw HTML
        card sample) so the parser can be wired. No writes."""
        results: list[dict[str, Any]] = []
        for path in paths:
            entry: dict[str, Any] = {"path": f"/api/admin/{path}"}
            try:
                resp = self._http.get(path, headers={**self._headers, "Accept": "application/json"})
                ct = resp.headers.get("content-type", "")
                entry.update(status=resp.status_code, content_type=ct)
                if "json" in ct:
                    body = resp.json()
                    entry["json"] = _truncate(body)
                    entry["count"] = len(body) if isinstance(body, list) else 1
                    results.append(entry)
                    continue

                html = resp.text
                # (a) embedded JSON: __NEXT_DATA__ (Next.js) and any application/json scripts
                m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
                if m:
                    try:
                        entry["__NEXT_DATA__"] = _truncate(json.loads(m.group(1)))
                    except ValueError:
                        pass
                scripts = []
                for block in re.findall(
                    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S
                )[:4]:
                    try:
                        scripts.append(_truncate(json.loads(block.strip())))
                    except ValueError:
                        pass
                if scripts:
                    entry["json_scripts"] = scripts
                # (b) how many appointments are in the page + a raw card sample for HTML parsing
                refs = re.findall(r"HF-[A-Z0-9-]{6,}", html)
                entry["ref_count"] = len(set(refs))
                if refs:
                    idx = html.find(refs[0])
                    entry["card_sample"] = html[max(0, idx - 1600) : idx + 400]
                # (c) referenced data endpoints
                api = sorted(set(re.findall(r"/api/[a-zA-Z0-9/_.\-]+", html)))
                entry["api_refs"] = [
                    a for a in api if not a.rstrip("/").endswith(("/calendar", "/bookings"))
                ][:15]
                results.append(entry)
            except (httpx.HTTPError, ValueError) as exc:
                entry["error"] = str(exc)
                results.append(entry)
        return results

    def list_appointments(self, window: dict[str, Any]) -> list[Appointment]:
        """All bookings from the calendar page's embedded data-booking JSON (read-only)."""
        rows = _bookings_from_html(self._get_text(LIST_PATH))
        return [_parse_appointment(r) for r in rows if r.get("ref")]

    def get_appointment(self, ref: str) -> Appointment | None:
        """One booking by its HF-… ref — scans the calendar (no per-id endpoint exists)."""
        for appt in self.list_appointments({}):
            if appt.ref == ref:
                return appt
        return None