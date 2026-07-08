"""thevea calendar connector (ADR-0003 D1/D5/D6).

The real thevea app is a single GraphQL endpoint at `mein.thevea.de/graphql` (HotChocolate);
auth is a **cookie session** established by a login mutation; no 2FA. See
`docs/spikes/M0-thevea-connector.md`.

The transport, session lifecycle, and error→`StateUnavailable` mapping below are complete and
tested against a mock transport. The **three GraphQL operations** and the two response-field
mappings are the only capture-dependent parts — fill them from the M0 DevTools capture (Network →
filter `graphql` → copy each operation's `query` and note the response shape). They are isolated
in the clearly-marked block so the rest of the connector needs no further change.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from laufwise.state.base import StateUnavailable, StateView

# ==========================================================================================
# CAPTURE-DEPENDENT — fill from the M0 DevTools capture. Nothing else in this file changes.
# ------------------------------------------------------------------------------------------
# 1. The exact GraphQL operation documents the app sends:
LOGIN_MUTATION = (
    "mutation Login($input: LoginInput!) { "
    "__PASTE_login_field__(input: $input) { __PASTE_selection__ } }"
)
AVAILABILITY_QUERY = (
    "query Availability($date: Date!, $type: String) { "
    "__PASTE_Terminfinder_or_Terminvorschlaege__(date: $date, type: $type) { __PASTE_fields__ } }"
)
BOOK_MUTATION = (
    "mutation Book($input: TerminInput!) { "
    "__PASTE_create_appointment_field__(input: $input) { __PASTE_fields__ } }"
)


def _login_variables(username: str, password: str) -> dict[str, Any]:
    # Match the shape the app sends (often wraps user/pass in an `input` object).
    return {"input": {"usernameOrEmail": username, "password": password}}


def _map_calendar_state(availability_data: dict[str, Any], want_slot: dict[str, Any]) -> dict[str, Any]:
    """Derive the checkable calendar state from the availability read, for the requested window.

    The template's checks read these keys:
      - `has_free_slot`  (precondition)  — is the requested window bookable?
      - `slot_booked`    (postcondition) — does our appointment now occupy the window?
    Fill the extraction from the real availability response shape. Placeholder logic below assumes
    a `slots` list of {start,end,free} objects — replace with the captured shape.
    """
    slots = availability_data.get("slots") or []
    start = want_slot.get("start")
    matching = [s for s in slots if s.get("start") == start] if start else slots
    return {
        "has_free_slot": any(s.get("free") for s in matching),
        "slot_booked": any(not s.get("free") for s in matching),
    }


# ==========================================================================================


class TheveaError(Exception):
    """Any failure talking to thevea — transport, HTTP, or a GraphQL-level error."""


class TheveaClient:
    """Cookie-session GraphQL client for one thevea account.

    The session cookie set by the login mutation lives on the httpx client's cookie jar and is
    sent automatically on subsequent calls. `transport` is injectable so the client is testable
    against canned responses without touching the network.
    """

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

    def __enter__(self) -> TheveaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def login(self) -> None:
        """Establish the session (sets the cookie on the client's jar)."""
        self._graphql(LOGIN_MUTATION, _login_variables(self._username, self._password))
        self._authed = True

    def availability(self, want_slot: dict[str, Any]) -> dict[str, Any]:
        """Read calendar availability for a window and map it to checkable state."""
        if not self._authed:
            self.login()
        data = self._graphql(
            AVAILABILITY_QUERY, {"date": want_slot.get("date"), "type": want_slot.get("type")}
        )
        return _map_calendar_state(data, want_slot)

    def book(self, want_slot: dict[str, Any]) -> dict[str, Any]:
        """Create the appointment. Returns the raw create payload (the tool only needs success)."""
        if not self._authed:
            self.login()
        return self._graphql(BOOK_MUTATION, {"input": want_slot})


class TheveaStateProvider:
    """Adapts a TheveaClient to the engine's StateProvider seam for the `calendar` binding.

    The requested window (date/type/start) travels from the binding's `params` -> the engine's
    `params["vars"]`. Any thevea failure becomes `StateUnavailable`, so an unreachable calendar
    BLOCKs the step (STATE_UNAVAILABLE) instead of masquerading as empty/free state.
    """

    def __init__(self, client: TheveaClient) -> None:
        self._client = client

    def query(self, name: str, params: dict | None = None) -> StateView:
        want_slot = (params or {}).get("vars") or {}
        try:
            state = self._client.availability(want_slot)
        except TheveaError as exc:
            raise StateUnavailable(f"thevea calendar unavailable: {exc}") from exc
        return StateView(value=state)