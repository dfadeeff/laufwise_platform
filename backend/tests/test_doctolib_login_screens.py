"""The doctolib login decides which screen it is on from the fields it can see, not from words.

The code screen used to be recognised by words like "einmal" or "per E-Mail" anywhere in the page
HTML, scripts included. After the PIN, doctolib showed another screen, the login took it for the
code screen, and the Studio waited for a code doctolib never sent.
"""

from __future__ import annotations

from app.providers.doctolib_login import _classify

AUTH = "https://auth.doctolib.de/pro/realms/doctolib-pro/protocol/openid-connect/auth"


def _f(type_, **attrs):
    return {"type": type_, **attrs}


def test_the_pin_screen():
    pin = [_f("password", maxlength="1", inputmode="numeric", autocomplete="one-time-code",
              label=f"Zeichen {i}/4") for i in range(1, 5)]
    assert _classify({"fields": pin, "text": "PIN PIN vergessen?"}, AUTH) == "pin"


def test_the_email_and_password_screens():
    assert _classify({"fields": [_f("email", autocomplete="username")], "text": "Einloggen"}, AUTH) == "email"
    assert _classify({"fields": [_f("password")], "text": "Passwort"}, AUTH) == "password"


def test_a_password_screen_that_mentions_email_is_not_the_code_screen():
    info = {"fields": [_f("password")], "text": "Passwort vergessen? Wir senden Ihnen einmal per E-Mail einen Link."}
    assert _classify(info, AUTH) == "password"


def test_the_code_screen_needs_code_boxes():
    six = [_f("text", maxlength="1") for _ in range(6)]
    assert _classify({"fields": six, "text": "Code"}, AUTH) == "code"
    one = [_f("text", autocomplete="one-time-code")]
    assert _classify({"fields": one, "text": "Bestätigungscode"}, AUTH) == "code"
    assert _classify({"fields": [_f("text")], "text": "Bitte den 6-stelligen Bestätigungscode eingeben"}, AUTH) == "code"


def test_inside_the_app_is_done():
    assert _classify({"fields": []}, "https://pro.doctolib.de/calendar/today/day") == "app"


def test_a_page_still_loading_is_unknown():
    assert _classify({"fields": [], "text": ""}, AUTH) == "unknown"
    assert _classify(None, AUTH) == "unknown"
