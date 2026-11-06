"""The login moves on as soon as doctolib shows the next screen, instead of sitting out fixed pauses.

A login took 3-5 minutes on the server: ~30s looking for a cookie banner a restored browser never
shows (three labels, 10s each), then 5-6s after every screen whether or not it had changed. The
page here is fake and its clock is virtual: `waited` is how long the login would have slept.
"""

from __future__ import annotations

from app.providers import doctolib_login as dl

EMAIL = {"headings": ["Einloggen"], "fields": [{"type": "email"}], "buttons": ["Weiter"]}
PASSWORD = {"headings": ["Passwort"], "fields": [{"type": "password"}], "buttons": ["Einloggen"]}
LOADING = {"headings": [], "fields": [], "buttons": []}


class _Button:
    def __init__(self, page):
        self.page, self.first = page, self

    def count(self):
        return 1 if self.page.banner else 0

    def is_visible(self):
        return self.page.banner

    def click(self, timeout=None):
        self.page.clicked += 1
        self.page.banner = False


class _Page:
    """Shows `screens[i]` while i polls have happened; the last one stays."""

    url = "https://auth.doctolib.de/pro/realms/doctolib-pro/protocol/openid-connect/auth"

    def __init__(self, screens, banner=False):
        self.screens, self.polls, self.waited = screens, 0, 0
        self.banner, self.clicked = banner, 0

    def evaluate(self, _js):
        info = self.screens[min(self.polls, len(self.screens) - 1)]
        self.polls += 1
        return info

    def wait_for_timeout(self, ms):
        self.waited += ms

    def get_by_role(self, _role, name=None):
        return _Button(self)


def test_the_next_screen_is_taken_as_soon_as_it_shows():
    page = _Page([EMAIL, LOADING, PASSWORD])
    dl._await_next_screen(page, dl._screen(page)[1])
    assert page.waited <= 1000


def test_a_screen_that_never_changes_is_given_up_on():
    page = _Page([PASSWORD])
    dl._await_next_screen(page, dl._screen(page)[1], timeout_ms=4000)
    assert 4000 <= page.waited <= 4500


def test_no_banner_is_not_waited_for_once_the_login_shows():
    page = _Page([EMAIL])
    dl._dismiss_consent(page)
    assert page.clicked == 0 and page.waited <= 2000


def test_the_banner_is_dismissed_when_it_shows():
    page = _Page([LOADING, EMAIL], banner=True)
    dl._dismiss_consent(page)
    assert page.clicked == 1
