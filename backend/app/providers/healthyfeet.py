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

from typing import Any

import httpx

from app.connectors.base import Appointment

# ==========================================================================================
# CAPTURE-DEPENDENT — fill from the source-API capture. Nothing else in this file changes.
# ------------------------------------------------------------------------------------------
LIST_PATH = "appointments"       # GET /api/admin/appointments?from=&to=
GET_PATH = "appointments/{ref}"  # GET /api/admin/appointments/{ref}


def _auth_headers(username: str, password: str, http: httpx.Client, base: str) -> dict[str, str]:
    """Return the auth header(s) for admin calls. Placeholder uses HTTP Basic; replace with the
    captured mechanism (e.g. POST a login to get a bearer token, then Authorization: Bearer …)."""
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _parse_appointment(row: dict[str, Any]) -> Appointment:
    """Map one source JSON row to an Appointment. Replace field names with the captured shape."""
    return Appointment(
        ref=str(row.get("id", "")),
        start=row.get("start", ""),
        end=row.get("end"),
        type=row.get("type"),
        patient=row.get("patient"),
        raw=row,
    )


# ==========================================================================================


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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._http.get(path, params=params, headers=self._headers)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"healthyfeet admin API error: {exc}") from exc

    def list_appointments(self, window: dict[str, Any]) -> list[Appointment]:
        data = self._get(LIST_PATH, params={"from": window.get("from"), "to": window.get("to")})
        rows = data if isinstance(data, list) else data.get("appointments", [])
        return [_parse_appointment(r) for r in rows]

    def get_appointment(self, ref: str) -> Appointment | None:
        try:
            data = self._get(GET_PATH.format(ref=ref))
        except SourceError:
            return None
        return _parse_appointment(data) if data else None