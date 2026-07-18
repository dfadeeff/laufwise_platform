"""Live read probe — does the REAL DoctolibConnector.list_appointments survive Cloudflare with a
replayed session, from a non-browser client? THROWAWAY. Run it YOURSELF so the session cookie
never leaves your machine; output is PII-free (counts / statuses / times only, no names/contacts).

Get a fresh session cookie from a logged-in browser:
  DevTools -> Application -> Cookies -> https://pro.doctolib.de -> copy the VALUE of
  `_doctolib_session` (and, if the plain run below gets blocked, also `__cf_bm`).

Run from backend/ with the venv:
  DOCTOLIB_SESSION='<_doctolib_session value>' \
  DOCTOLIB_AGENDAS='2570190,2557171' \
  DOCTOLIB_DATE='2026-07-18' \
    ./.venv/bin/python spikes/doctolib_live_read.py

  # if it comes back blocked (HTML/403), add the Cloudflare token and retry:
  DOCTOLIB_CFBM='<__cf_bm value>' ... same command

GREEN = a count > 0 with statuses/times printed. That proves the read path works server-side;
proceed to deploy. RED (HTML/403) = Cloudflare blocks a bare replay -> we need a different
transport (browser-cookie incl. __cf_bm, or a headless-browser fetch), before deploying.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

# make `app` importable when run as spikes/doctolib_live_read.py from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.providers.doctolib import DoctolibConnector, DoctolibError


def main() -> int:
    session = os.environ.get("DOCTOLIB_SESSION")
    if not session:
        print("set DOCTOLIB_SESSION (the _doctolib_session cookie value)", file=sys.stderr)
        return 2
    agendas = os.environ.get("DOCTOLIB_AGENDAS", "2570190,2557171")
    date = os.environ.get("DOCTOLIB_DATE")  # YYYY-MM-DD; default = connector's forward scan
    window = {"from": date, "to": date} if date else {}

    conn = DoctolibConnector(
        "https://pro.doctolib.de", "", "", agenda_ids=agendas, session_cookie=session
    )
    # Optional: also replay Cloudflare's bot-management token if a bare session gets challenged.
    cfbm = os.environ.get("DOCTOLIB_CFBM")
    if cfbm:
        conn._http.cookies.set("__cf_bm", cfbm, domain="pro.doctolib.de")

    try:
        appts = conn.list_appointments(window)
    except DoctolibError as exc:
        print(f"\nRED: read failed -> {exc}")
        _diagnose(session, cfbm, agendas.split(",")[0], date)
        return 1
    finally:
        conn.close()

    print(f"\nGREEN: read {len(appts)} appointment(s) across agendas [{agendas}]"
          + (f" for {date}" if date else " (forward scan)"))
    by_status = Counter((a.raw or {}).get("status") for a in appts)
    print(f"  by status: {dict(by_status)}")
    importable = sum(1 for a in appts if (a.raw or {}).get("status") == "confirmed")
    print(f"  confirmed (would import): {importable}")
    for a in appts[:5]:  # PII-free: time + truncated opaque ref + status only
        print(f"    {a.start}  status={a.raw.get('status')}  ref={a.ref[:12]}…")
    return 0


def _diagnose(session: str, cfbm: str | None, agenda: str, date: str | None) -> None:
    """Show the raw HTTP status + content-type so we can tell a Cloudflare challenge (HTML/403)
    apart from an expired session (302/login) — without dumping the body."""
    cookies = {"_doctolib_session": session}
    if cfbm:
        cookies["__cf_bm"] = cfbm
    try:
        r = httpx.get(
            "https://pro.doctolib.de/appointments",
            params={"agenda_ids": agenda, "start_date": f"{date or '2026-07-18'} 00:00:00",
                    "end_date": f"{date or '2026-07-18'} 23:59:59", "view": "day",
                    "include_patients": "true"},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            cookies=cookies, follow_redirects=False, timeout=20,
        )
        print(f"  raw: HTTP {r.status_code}  content-type={r.headers.get('content-type')}"
              f"  server={r.headers.get('server')}  cf-ray={r.headers.get('cf-ray')}")
        if r.status_code in (301, 302):
            print(f"  -> redirect to {r.headers.get('location', '?')[:60]}… (session likely expired)")
    except httpx.HTTPError as exc:
        print(f"  raw probe transport error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
