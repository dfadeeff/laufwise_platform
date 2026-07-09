"""Source-capture spike (ADR-0004) — prove the agent can reach healthyfeet's admin calendar and
see the appointment shape. THROWAWAY (like thevea_login_spike): not imported by app code.

Recon established (unauthenticated):
  - Base:   https://www.healthyfeet-podologie.de/api/admin   (Vercel / Next.js API routes)
  - Auth:   HTTP Basic  (WWW-Authenticate: Basic realm="Healthy Feet Admin")
  - Routes: /api/admin/calendar and /api/admin/bookings exist (401 behind Basic); bogus creds
            are rejected, so real credentials are validated.

What YOU supply (your admin Basic-auth credentials — the "preset" source creds):
    HEALTHYFEET_ADMIN_USER, HEALTHYFEET_ADMIN_PASSWORD

Run:
    HEALTHYFEET_ADMIN_USER='...' HEALTHYFEET_ADMIN_PASSWORD='...' \
      ../.venv/bin/python healthyfeet_source_spike.py

Green = a 200 + JSON from /calendar and/or /bookings. That IS the agent reading the admin
calendar. Copy the JSON shape into app/providers/healthyfeet.py (_parse_appointment + LIST_PATH),
and the source connector is live.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = "https://www.healthyfeet-podologie.de/api/admin"
CANDIDATES = ["calendar", "bookings"]  # the two routes recon found; capture shows which holds appts


def main() -> int:
    user = os.environ.get("HEALTHYFEET_ADMIN_USER")
    pwd = os.environ.get("HEALTHYFEET_ADMIN_PASSWORD")
    if not user or not pwd:
        print("set HEALTHYFEET_ADMIN_USER and HEALTHYFEET_ADMIN_PASSWORD", file=sys.stderr)
        return 2

    ok = False
    with httpx.Client(base_url=BASE, timeout=15, auth=(user, pwd), follow_redirects=True) as client:
        for path in CANDIDATES:
            print(f"\n=== GET {BASE}/{path} ===")
            try:
                resp = client.get(f"/{path}")
            except httpx.HTTPError as exc:
                print(f"  transport error: {exc}")
                continue
            print(f"  HTTP {resp.status_code}  ({resp.headers.get('content-type')})")
            if resp.status_code == 401:
                print("  -> credentials rejected for this route")
                continue
            if resp.status_code != 200:
                print(f"  -> unexpected: {resp.text[:200]}")
                continue
            ok = True
            try:
                body = resp.json()
            except ValueError:
                print(f"  -> non-JSON body: {resp.text[:300]}")
                continue
            # Show the shape so you can map it into the connector.
            preview = body[:2] if isinstance(body, list) else body
            print("  JSON shape:")
            print("  " + json.dumps(preview, indent=2, ensure_ascii=False)[:1200].replace("\n", "\n  "))
            n = len(body) if isinstance(body, list) else "1 object"
            print(f"  -> {n} record(s). This is the agent reading the admin calendar. ✅")

    if ok:
        print("\nSOURCE ACCESS GREEN. Map the JSON into app/providers/healthyfeet.py and you're live.")
        return 0
    print("\nNo route returned data — check the credentials and which route holds appointments.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
