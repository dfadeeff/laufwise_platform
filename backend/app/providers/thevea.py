"""thevea destination connector (ADR-0003 auth + ADR-0004 role).

thevea is the **destination** of a governed import: it can `find_appointment` (by the source ref
we tag on create) and `create_appointment` — and nothing else. There is no update or delete, so
the import is append-only / never-replace by construction (ADR-0004 D7).

Auth is a cookie session over the GraphQL endpoint `mein.thevea.de/graphql` (HotChocolate, no
2FA; see docs/spikes/M0-thevea-connector.md). The transport + session + error->TheveaError
mapping are complete and tested against a mock transport. The GraphQL operations and the
appointment field mappings are the capture-dependent fill-ins, isolated in the marked block.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.connectors.base import Appointment

# ==========================================================================================
# CAPTURE-DEPENDENT — fill from the M0 DevTools capture. Nothing else in this file changes.
# ------------------------------------------------------------------------------------------
LOGIN_MUTATION = (
    "mutation Login($input: LoginInput!) { "
    "__PASTE_login_field__(input: $input) { __PASTE_selection__ } }"
)
# Find a previously-imported appointment by the source ref we stored on it (idempotency + verify).
FIND_QUERY = (
    "query FindByRef($ref: String!) { "
    "__PASTE_appointments_field__(externalRef: $ref) { __PASTE_fields__ } }"
)
# Create an appointment, tagged with the source ref so it is findable and de-duplicated.
CREATE_MUTATION = (
    "mutation CreateTermin($input: TerminInput!) { "
    "__PASTE_create_field__(input: $input) { __PASTE_fields__ } }"
)


def _login_variables(username: str, password: str) -> dict[str, Any]:
    return {"input": {"usernameOrEmail": username, "password": password}}


def _parse_found(data: dict[str, Any]) -> Appointment | None:
    """Map the find-query response to an Appointment, or None if not present. Replace the field
    path with the captured shape (placeholder assumes a `nodes` list under the query field)."""
    for value in data.values():
        nodes = value.get("nodes") if isinstance(value, dict) else None
        if nodes:
            n = nodes[0]
            return Appointment(ref=str(n.get("externalRef", "")), start=n.get("start", ""), raw=n)
    return None


def _create_input(appt: Appointment) -> dict[str, Any]:
    """Map an Appointment to thevea's create-input, tagging it with the source ref."""
    return {"input": {"externalRef": appt.ref, "start": appt.start, "end": appt.end, "type": appt.type}}


# ==========================================================================================


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
        transport: httpx.BaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._url = base_url.rstrip("/") + "/graphql"
        self._username = username
        self._password = password
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True)
        self._authed = False

    def close(self) -> None:
        self._http.close()

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._http.post(
                self._url,
                json={"query": query, "variables": variables or {}},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise TheveaError(f"thevea transport error: {exc}") from exc
        if body.get("errors"):
            raise TheveaError(f"thevea graphql error: {body['errors']}")
        return body.get("data") or {}

    def _ensure_auth(self) -> None:
        if not self._authed:
            self._graphql(LOGIN_MUTATION, _login_variables(self._username, self._password))
            self._authed = True

    def find_appointment(self, ref: str) -> Appointment | None:
        self._ensure_auth()
        return _parse_found(self._graphql(FIND_QUERY, {"ref": ref}))

    def create_appointment(self, appt: Appointment) -> None:
        self._ensure_auth()
        self._graphql(CREATE_MUTATION, _create_input(appt))