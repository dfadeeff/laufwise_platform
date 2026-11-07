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

import json
import logging
import re
from typing import Any, Callable

log = logging.getLogger(__name__)

SIGNIN_URL = "https://pro.doctolib.de/signin"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def _click_first(page, labels: list[str], *, timeout_ms: int = 3000) -> str | None:
    """Click the first of `labels` that shows up — WAITING for it, briefly.

    doctolib renders the consent banner and each step's button asynchronously, so a query that runs
    the instant the page settles finds nothing and silently clicks nothing. That is not harmless:
    the un-dismissed consent overlay then covers the email field, and the fill that follows waits
    30s for an element it can see but cannot reach. Verified live 2026-08-12 — the login failed at
    `waiting for locator("input[type=email]")` for exactly this reason.

    Matched by accessible name, case-insensitively: the consent buttons render as `ABLEHNEN`.
    """
    for lbl in labels:
        try:
            page.get_by_role(
                "button", name=re.compile(rf"^\s*{re.escape(lbl)}", re.IGNORECASE)
            ).first.click(timeout=timeout_ms)
            return lbl
        except Exception:  # noqa: BLE001 — this label isn't on screen; try the next one
            continue
    return None


def _doctolib_state(state: dict[str, Any]) -> str:
    """The browser's doctolib cookies and site storage, as JSON — nothing from other sites.

    `pin_login` alone was not enough: with it doctolib skips the e-mail field but still e-mails a
    code, so whatever marks a known device lives elsewhere in the browser. Keeping the whole
    doctolib part of it is what a remembered browser does.
    """
    return json.dumps({
        "cookies": [c for c in state.get("cookies", []) if "doctolib.de" in c.get("domain", "")],
        "origins": [o for o in state.get("origins", []) if "doctolib.de" in o.get("origin", "")],
    })


# What a page shows: headings, the fields' attributes and labels, and button names — never what is
# typed into a field (`value` is not read).
_PAGE_INFO_JS = """() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const txt = e => (e.innerText || e.textContent || '').trim().replace(/\\s+/g, ' ');
  const label = i => {
    const l = (i.id && document.querySelector(`label[for="${i.id}"]`)) || i.closest('label');
    return l ? txt(l) : (i.getAttribute('aria-label') || '');
  };
  return {
    headings: [...document.querySelectorAll('h1,h2,h3,legend')].filter(vis).map(txt),
    fields: [...document.querySelectorAll('input,select,textarea')].filter(vis)
      .filter(i => i.type !== 'hidden').map(i => ({
        type: i.type || i.tagName.toLowerCase(), name: i.name || '',
        inputmode: i.getAttribute('inputmode') || '', maxlength: i.getAttribute('maxlength') || '',
        autocomplete: i.getAttribute('autocomplete') || '', placeholder: i.placeholder || '',
        label: label(i),
      })),
    buttons: [...document.querySelectorAll('button,[role=button],input[type=submit]')].filter(vis)
      .map(b => txt(b) || b.value || b.getAttribute('aria-label') || '').filter(Boolean),
    text: (document.body ? document.body.innerText : '').slice(0, 4000),
  };
}"""

_FIELD_KEYS = ("type", "name", "inputmode", "maxlength", "autocomplete", "placeholder", "label")


def _page_summary(info: dict[str, Any] | None) -> str:
    """One line saying which page the login stopped on, from `_PAGE_INFO_JS`'s answer."""
    info = info or {}

    def field(f: dict[str, Any]) -> str:
        parts = [str(f.get("type") or "?")] + [
            f"{k}={f[k]}" for k in _FIELD_KEYS[1:] if f.get(k)
        ]
        return "[" + " ".join(parts) + "]"

    headings = ", ".join(str(h)[:80] for h in info.get("headings") or []) or "-"
    fields = " ".join(field(f) for f in info.get("fields") or []) or "-"
    buttons = ", ".join(str(b)[:40] for b in info.get("buttons") or []) or "-"
    return f"headings: {headings} · fields: {fields} · buttons: {buttons}"[:600]


def _describe(page) -> str:
    try:
        return _page_summary(page.evaluate(_PAGE_INFO_JS))
    except Exception:  # noqa: BLE001 — a page that cannot be read must not hide the real error
        return "the page could not be read"


# doctolib's PIN screen: four one-digit password fields (`Zeichen 1/4` … `4/4`), shown to a
# device it trusts instead of the e-mailed code. One-digit boxes are never the password field.
_PIN_BOX = "input[type=password][maxlength='1']:visible"
_PASSWORD = "input[type=password]:not([maxlength='1']):visible"


def _pin_digits(pin: str) -> str:
    """The stored PIN's four digits, or a DoctolibError that says how to give one."""
    from app.providers.doctolib import DoctolibLoginRefused

    digits = "".join(c for c in pin if c.isdigit())
    if len(digits) != 4:
        raise DoctolibLoginRefused(
            "doctolib asks for the 4-digit PIN of this account. Open the doctolib connection in "
            "the studio, choose \"Login details changed?\" and enter the PIN — it is stored "
            "encrypted, like the password.",
            "pin",
        )
    return digits


def _enter_code(page, raw: str) -> None:
    """Type the e-mailed 6-digit code, tick 'trust this device', and confirm."""
    digits = "".join(c for c in raw if c.isdigit())[:6]
    boxes = [
        x for x in page.query_selector_all("input[type=text], input[type=tel], input:not([type])")
        if x.is_visible()
    ]
    if len(boxes) >= 6:
        for i in range(6):
            boxes[i].fill(digits[i])
    elif boxes:
        boxes[0].click()
        page.keyboard.type(digits, delay=120)  # auto-advances across boxes
    page.wait_for_timeout(1200)
    cb = page.query_selector("input[type=checkbox]")  # 'trust this device'
    if cb:
        try:
            cb.check()
        except Exception:  # noqa: BLE001 — best-effort; absence is fine
            pass
    if _screen(page)[0] == "code":  # a code that submits itself has already moved the page on
        _click_first(page, ["Bestätigen", "Fortfahren", "Weiter", "Einloggen", "Verifizieren"])


def _enter_pin_if_asked(page, pin: str) -> bool:
    """Type the PIN when doctolib shows its PIN screen. True when it did."""
    boxes = page.query_selector_all(_PIN_BOX)
    if len(boxes) != 4:
        return False
    digits = _pin_digits(pin)
    boxes[0].click()
    page.keyboard.type(digits, delay=150)  # the boxes advance by themselves, like the code boxes
    # No button to press: doctolib's PIN screen submits itself on the fourth digit, and a click
    # here would land on the NEXT screen's button (an empty password submitted). The walk then
    # waits for that next screen.
    return True


_CODE_WORDS = ("bestätigungscode", "6-stellig")


def _on_app(url: str) -> bool:
    return "pro.doctolib.de" in url and "/signin" not in url and "auth.doctolib.de" not in url


def _classify(info: dict[str, Any] | None, url: str) -> str:
    """Which login screen this is — app | pin | code | email | password | unknown — from the
    visible fields (`_PAGE_INFO_JS`), never from words hidden in the HTML.

    The code screen is the one with code boxes: six one-digit text boxes, or a non-password
    one-time-code field, or a text field under visible words that name the code.
    """
    if _on_app(url):
        return "app"
    info = info or {}
    fields = info.get("fields") or []
    text = str(info.get("text") or "").lower()
    one_digit = [f for f in fields if str(f.get("maxlength")) == "1"]
    if sum(1 for f in one_digit if f.get("type") == "password") == 4:
        return "pin"
    texty = [f for f in fields if f.get("type") in ("text", "tel", "number")]
    if (
        sum(1 for f in one_digit if f.get("type") != "password") >= 6
        or any(f.get("autocomplete") == "one-time-code" for f in texty)
        or (texty and any(w in text for w in _CODE_WORDS))
    ):
        return "code"
    if any(f.get("type") == "email" for f in fields):
        return "email"
    if any(f.get("type") == "password" for f in fields):
        return "password"
    return "unknown"


def _screen(page) -> tuple[str, str]:
    """The current screen and its description, for the walk and for the log."""
    try:
        info = page.evaluate(_PAGE_INFO_JS)
    except Exception:  # noqa: BLE001 — a page mid-navigation cannot be read; look again
        info = None
    return _classify(info, page.url), _page_summary(info)


_POLL_MS = 300
_CONSENT = re.compile(r"^\s*(Ablehnen|Alle ablehnen|Reject)", re.IGNORECASE)


def _await_next_screen(page, before: str, *, timeout_ms: int = 15000) -> None:
    """Return as soon as the page shows a screen other than `before` (the summary of the one just
    answered), or once `timeout_ms` has passed. A page still loading is not a new screen."""
    for _ in range(timeout_ms // _POLL_MS + 1):
        page.wait_for_timeout(_POLL_MS)
        screen, shown = _screen(page)
        if screen == "app" or (screen != "unknown" and shown != before):
            return


def _dismiss_consent(page, *, timeout_ms: int = 10000, grace_ms: int = 1500) -> None:
    """Reject the cookie banner when it shows, without waiting for one that never will.

    A restored browser remembers the answer, so the banner usually does not come at all; waiting
    the full budget for it cost ~30s a login. Once a login screen is up, the banner gets
    `grace_ms` more to appear — it renders late, and left up it covers the fields (2026-08-12).
    """
    shown_for = 0
    for _ in range(timeout_ms // _POLL_MS + 1):
        try:
            button = page.get_by_role("button", name=_CONSENT)
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                return
        except Exception:  # noqa: BLE001 — a page mid-render; look again
            pass
        if _screen(page)[0] != "unknown":
            shown_for += _POLL_MS
            if shown_for >= grace_ms:
                return
        page.wait_for_timeout(_POLL_MS)


def headless_login(
    username: str,
    password: str,
    get_code: Callable[[], str | None],
    *,
    pin_login: str = "",
    browser_state: str = "",
    pin: str = "",
    headless: bool = True,
    timeout_ms: int = 60000,
) -> dict[str, str]:
    """Drive the real 2-step doctolib Pro login in a headless browser and return the authenticated
    cookies: ``{"_doctolib_session": ..., "pin_login": ..., "browser_state": ...}``.

    `browser_state` is the doctolib part of the browser from an earlier login (`_doctolib_state`).
    It is restored before the first page loads, so the login runs in the browser doctolib saw.

    `pin_login` is a device-trust cookie from an EARLIER login (they last ~3 months). Presenting it
    is what makes an unattended login possible at all: without it doctolib treats every login as a
    new device and emails a code, which nothing can answer during a background import. This
    function could previously only *capture* that cookie, never present one.

    `get_code` is invoked ONLY when the email code is still required; it must block until the user
    provides the 6-digit code (or return None to abort). Raises DoctolibError on any failure.
    """
    from playwright.sync_api import sync_playwright  # lazy — only imported when a login runs

    from app.providers.doctolib import DoctolibError, DoctolibLoginRefused

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = browser.new_context(
                user_agent=_UA, locale="de-DE", viewport={"width": 1280, "height": 900},
                storage_state=json.loads(browser_state) if browser_state else None,
            )
            if pin_login:
                # Set BEFORE the first navigation: doctolib decides whether this is a known device
                # while serving the login page, so a cookie added later is too late to skip the
                # email code. Domain is dotted so it rides on pro.doctolib.de and its siblings.
                ctx.add_cookies(
                    [{"name": "pin_login", "value": pin_login, "domain": ".doctolib.de", "path": "/"}]
                )
            page = ctx.new_page()
            # Not "networkidle": doctolib's trackers keep the network busy long after the form is
            # usable. The consent wait below and the walk both wait for what is actually shown.
            page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)

            # Consent first: leaving it up blocks every field underneath it.
            _dismiss_consent(page)

            # Walk whatever screens doctolib shows, in whatever order: a trusted device can get
            # the PIN before the password, after it, or instead of the e-mailed code. Each screen
            # is answered once; one that comes back after its answer was refused.
            code_seen = {"yes": False}
            answered: set[str] = set()
            unknown = 0
            for _ in range(10):
                screen, shown = _screen(page)
                log.info("doctolib login screen: %s — %s", screen, shown)
                if screen == "app":
                    break
                if screen == "unknown":
                    unknown += 1
                    if unknown > 5:
                        break
                    _await_next_screen(page, shown, timeout_ms=2000)
                    continue
                if screen in answered:
                    if screen == "pin":
                        raise DoctolibLoginRefused("doctolib did not accept the PIN", "pin")
                    if screen == "password" and "ungültig" in page.inner_text("body").lower():
                        raise DoctolibLoginRefused(
                            "doctolib rejected the username or password", "password"
                        )
                    break
                answered.add(screen)
                if screen == "email":
                    page.fill("input[type=email]:visible", username)
                    _click_first(page, ["Weiter", "Continue"])
                elif screen == "password":
                    page.fill(_PASSWORD, password)  # hidden mirrors sync from the visible field
                    _click_first(page, ["Einloggen", "Anmelden", "Weiter"])
                elif screen == "pin":
                    _enter_pin_if_asked(page, pin)
                elif screen == "code":
                    code_seen["yes"] = True
                    raw = get_code()
                    if not raw:
                        # Nobody is at the keyboard — a background import re-logging in. Say
                        # what the operator has to do, since no amount of retrying will fix it.
                        raise DoctolibError(
                            "doctolib asked for the code it e-mails, and an import cannot answer "
                            "it. Reconnect doctolib in the studio (the form takes the code), then "
                            "run the import right after."
                        )
                    _enter_code(page, raw)
                _await_next_screen(page, shown)

            # completion = redirect back to the app, which sets the authenticated session
            try:
                page.wait_for_url("**pro.doctolib.de/**", timeout=35000)
            except Exception:  # noqa: BLE001 — fall through to the explicit auth check below
                pass
            page.wait_for_timeout(3000)

            cookies = {c["name"]: c["value"] for c in ctx.cookies() if "doctolib.de" in c["domain"]}
            # Names only, never values: whether a code was asked for, with what the browser held,
            # is how a reconnect that still needs a code gets explained.
            log.info(
                "doctolib login: restored state=%s, code requested=%s, cookies after=%s",
                bool(browser_state), code_seen["yes"], sorted(cookies),
            )
            session = cookies.get("_doctolib_session")
            # `_doctolib_session` exists even pre-auth, so presence alone isn't proof. Completion =
            # we ended up inside the app (pro.doctolib.de) and NOT back on the login pages. This is
            # exactly the signal the production proof used (it landed on /calendar/today/day); a bad
            # code or bad credentials leaves us on signin/auth.doctolib.de instead.
            url = page.url
            on_app = "pro.doctolib.de" in url and "/signin" not in url and "auth.doctolib.de" not in url
            if not session or not on_app:
                # Say WHERE it stopped. The same credentials complete this login from a home
                # connection and fail from the server, so "wrong password?" is usually the wrong
                # suspicion — the useful facts are the page it ended on and whether a code was ever
                # asked for. Guessing between a bot wall, a rejected password and an unanswered
                # code, with nothing to go on, costs an hour every time.
                stage = (
                    "a code was requested and submitted" if code_seen["yes"]
                    else "no code was ever requested"
                )
                shown = _describe(page)
                log.warning("doctolib login stopped on %s: %s", url.split("?")[0], shown)
                raise DoctolibError(
                    f"doctolib login did not complete — {stage}, and the browser ended on "
                    f"{url.split('?')[0]!r} (title {page.title()!r}). The page shows: {shown}"
                )

            return {
                "_doctolib_session": session,
                "pin_login": cookies.get("pin_login", ""),
                "browser_state": _doctolib_state(ctx.storage_state()),
            }
        finally:
            browser.close()
