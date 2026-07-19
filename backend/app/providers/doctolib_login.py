"""Headless-browser login for doctolib Pro (Option A) — the mechanism only.

Isolated in its own module so the READ path (app.providers.doctolib, pure httpx) never imports
Playwright: reads stay lightweight, and only an actual *login* pulls in a browser.

Why a browser at all: doctolib Pro login is a JS-rendered Keycloak form gated by friendlyCaptcha
(a browser-run proof-of-work). A raw HTTP POST is rejected as "invalid credentials" because it
can't produce the captcha token; a real (headless) browser runs the captcha JS and passes. This
was verified live from a residential IP: the flow below cleared the captcha, accepted the
password, and reached the new-device email-code screen.

The function returns the authenticated cookies — `_doctolib_session` (replayed by the read
connector) and `pin_login` (the ~3-month device-trust cookie, so later logins can skip the email
code). `get_code` is the seam for the two-step UX: it is called only when doctolib demands the
email code, and should block until the user supplies it (or return None to abort).

VERIFICATION STATUS: the login-up-to-the-code-screen is verified live. The code-submission +
final redirect + session capture is transcribed from the prototype and is NOT yet confirmed
end-to-end (the prototype's OTP redirect didn't complete once). It needs one controlled live run
to confirm — do NOT assume the returned session authenticates until that run is green. WHERE this
runs (Railway datacenter IP vs. the user's machine) is an open architecture decision — see the
A1/A2 fork; a datacenter-IP login is unproven.
"""

from __future__ import annotations

from typing import Callable

SIGNIN_URL = "https://pro.doctolib.de/signin"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def _click_first(page, labels: list[str]) -> str | None:
    for lbl in labels:
        el = page.query_selector(f"button:has-text('{lbl}')")
        if el and el.is_visible():
            el.click()
            return lbl
    return None


def _is_code_page(body_lower: str) -> bool:
    return any(k in body_lower for k in ("bestätigungscode", "per e-mail", "einmal", "6-stellig"))


def headless_login(
    username: str,
    password: str,
    get_code: Callable[[], str | None],
    *,
    headless: bool = True,
    timeout_ms: int = 60000,
) -> dict[str, str]:
    """Drive the real 2-step doctolib Pro login in a headless browser and return the authenticated
    cookies: ``{"_doctolib_session": ..., "pin_login": ...}``.

    `get_code` is invoked ONLY when the new-device email code is required; it must block until the
    user provides the 6-digit code (or return None to abort). Raises DoctolibError on any failure.
    """
    from playwright.sync_api import sync_playwright  # lazy — only imported when a login runs

    from app.providers.doctolib import DoctolibError

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = browser.new_context(
                user_agent=_UA, locale="de-DE", viewport={"width": 1280, "height": 900}
            )
            page = ctx.new_page()
            page.goto(SIGNIN_URL, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_timeout(2000)

            _click_first(page, ["Ablehnen"])  # dismiss cookie consent
            page.wait_for_timeout(800)

            # step 1 — email
            page.fill("input[type=email]", username)
            _click_first(page, ["Weiter", "Continue"])

            # step 2 — the visible password field (hidden mirrors sync from it)
            page.wait_for_selector("input[type=password]:visible", timeout=30000)
            page.fill("input[type=password]:visible", password)
            _click_first(page, ["Einloggen", "Anmelden"])
            page.wait_for_timeout(8000)

            body = page.content().lower()
            if "ungültig" in body and not _is_code_page(body):
                raise DoctolibError("doctolib rejected the username or password")

            # new-device email code (skipped when pin_login already trusts this browser)
            if _is_code_page(body):
                raw = get_code()
                if not raw:
                    raise DoctolibError("doctolib requires an email code to finish connecting")
                digits = "".join(c for c in raw if c.isdigit())[:6]
                boxes = [
                    x for x in page.query_selector_all("input[type=text], input:not([type])")
                    if x.is_visible()
                ]
                if len(boxes) >= 6:
                    for i in range(6):
                        boxes[i].fill(digits[i])
                elif boxes:
                    boxes[0].click()
                    page.keyboard.type(digits, delay=120)  # auto-advances across boxes
                page.wait_for_timeout(1200)
                cb = page.query_selector("input[type=checkbox]")  # 'trust this device' -> pin_login
                if cb:
                    try:
                        cb.check()
                    except Exception:  # noqa: BLE001 — best-effort; absence is fine
                        pass
                _click_first(page, ["Bestätigen", "Fortfahren", "Weiter", "Einloggen", "Verifizieren"])

            # completion = redirect back to the app, which sets the authenticated session
            try:
                page.wait_for_url("**pro.doctolib.de/**", timeout=35000)
            except Exception:  # noqa: BLE001 — fall through to the explicit auth check below
                pass
            page.wait_for_timeout(3000)

            cookies = {c["name"]: c["value"] for c in ctx.cookies() if "doctolib.de" in c["domain"]}
            session = cookies.get("_doctolib_session")
            # `_doctolib_session` exists even pre-auth, so presence alone isn't proof. Completion =
            # we ended up inside the app (pro.doctolib.de) and NOT back on the login pages. This is
            # exactly the signal the production proof used (it landed on /calendar/today/day); a bad
            # code or bad credentials leaves us on signin/auth.doctolib.de instead.
            url = page.url
            on_app = "pro.doctolib.de" in url and "/signin" not in url and "auth.doctolib.de" not in url
            if not session or not on_app:
                raise DoctolibError(
                    "doctolib login did not complete (still on the login page — wrong code or credentials?)"
                )

            return {"_doctolib_session": session, "pin_login": cookies.get("pin_login", "")}
        finally:
            browser.close()
