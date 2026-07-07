"""M0 spike — prove headless login + one authenticated calendar read against thevea.

THROWAWAY. Not app code, not imported anywhere. Deleted once M1's TheveaClient exists.

Recon (docs/spikes/M0-thevea-connector.md) established:
  - App API:   https://mein.thevea.de/graphql   (HotChocolate / .NET, introspection off)
  - Transport: Apollo, POST, same-origin, COOKIE session (no bearer token)
  - No 2FA / CAPTCHA on login

What YOU must supply (10 min in browser DevTools -> Network -> filter "graphql"):
  1. The LOGIN operation: its `query` string + `variables` shape. Paste into LOGIN_MUTATION
     / login_variables below. Field name is NOT `login`/`loginUser` (both confirmed absent).
  2. One AVAILABILITY read (the Terminfinder / Terminvorschlaege query). Paste into READ_QUERY.
  3. Credentials via env: THEVEA_USER, THEVEA_PASS.

Run:
    THEVEA_USER='you@practice.de' THEVEA_PASS='...' \
      ../.venv/bin/python thevea_login_spike.py

Green = a session cookie is obtained AND the authenticated read returns data.
That closes M0; proceed to M1 (the real connector).
"""

from __future__ import annotations

import os
import sys

import httpx  # already in the backend venv

GRAPHQL_URL = "https://mein.thevea.de/graphql"

# --- PASTE FROM DEVTOOLS -----------------------------------------------------
# The exact login mutation the app sends. Replace the placeholder body/field names
# with what you captured. Keep it a valid GraphQL document string.
LOGIN_MUTATION = """
mutation Login($input: <PASTE_INPUT_TYPE>) {
  <PASTE_LOGIN_FIELD>(input: $input) {
    # paste the exact selection set the app requests
    __typename
  }
}
"""

# The variables object the app sends for login (usually wraps user/pass in an input).
def login_variables(user: str, password: str) -> dict:
    return {
        "input": {
            # e.g. "usernameOrEmail": user, "password": password  -- match the captured shape
            "usernameOrEmail": user,
            "password": password,
        }
    }

# An authenticated read to prove the session works — the availability / slot-finder query.
READ_QUERY = """
query Verify {
  # paste a small authenticated query, e.g. current user or today's appointments:
  # termineForToday { nodes { id start end } }
  __typename
}
"""
# -----------------------------------------------------------------------------


def _post(client: httpx.Client, query: str, variables: dict | None = None) -> httpx.Response:
    return client.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables or {}},
        headers={"Content-Type": "application/json"},
    )


def main() -> int:
    user = os.environ.get("THEVEA_USER")
    password = os.environ.get("THEVEA_PASS")
    if not user or not password:
        print("set THEVEA_USER and THEVEA_PASS in the environment", file=sys.stderr)
        return 2

    if "<PASTE" in LOGIN_MUTATION or "<PASTE" in READ_QUERY:
        print(
            "Fill LOGIN_MUTATION / login_variables / READ_QUERY from a DevTools capture first "
            "(see the module docstring).",
            file=sys.stderr,
        )
        return 2

    # A cookie jar is the whole point: if the session is cookie-based, login writes a cookie
    # here and the follow-up read reuses it automatically.
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        print(f"POST login -> {GRAPHQL_URL}")
        r = _post(client, LOGIN_MUTATION, login_variables(user, password))
        print(f"  HTTP {r.status_code}")
        body = r.json()
        if body.get("errors"):
            print("  GraphQL errors:", body["errors"])
            print("  -> login op/shape is wrong, or credentials rejected. Fix and rerun.")
            return 1

        cookies = dict(client.cookies)
        print(f"  session cookies set: {list(cookies) or 'NONE (session may be token-based!)'}")
        print(f"  login data: {body.get('data')}")

        print("POST authenticated read")
        r2 = _post(client, READ_QUERY)
        print(f"  HTTP {r2.status_code}")
        body2 = r2.json()
        if body2.get("errors"):
            print("  GraphQL errors:", body2["errors"])
            print("  -> read failed (session not carried? unauthorized?).")
            return 1

        print(f"  read data: {body2.get('data')}")

    print("\nM0 GREEN: headless login + authenticated read both succeeded.")
    print("Confirm which cookie carried the session, note its lifetime, then start M1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())