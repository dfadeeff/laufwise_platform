"""thevea's login answers with a union: the account on success, a typed refusal otherwise.

Since 2026-11-05 thevea rejects a login that selects `benutzerkennung` directly on that union, and
every import stopped with `state_unavailable`. A wrong password is no GraphQL error either — it is
the `ZugangsdatenUngueltig` member — so a login that only checks for errors would think it got in.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.providers.thevea import TheveaConnector, TheveaError


def _connector(answer):
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["query"] = body["query"]
        return httpx.Response(200, json={"data": {"benutzerLogin": answer}})

    return TheveaConnector("https://mein.thevea.de", "u", "p", transport=httpx.MockTransport(handler)), seen


def test_the_login_selects_the_account_through_a_fragment():
    conn, seen = _connector({"__typename": "EmailBenutzerZugang", "benutzerkennung": "u"})
    conn.verify()
    assert "... on BenutzerZugang { benutzerkennung }" in seen["query"]


@pytest.mark.parametrize("refusal", ["ZugangsdatenUngueltig", "EingabeFehlerhaft", "DomainInvalid"])
def test_a_refused_login_raises_instead_of_counting_as_logged_in(refusal):
    conn, _ = _connector({"__typename": refusal})
    with pytest.raises(TheveaError, match=refusal):
        conn.verify()
    assert conn._authed is False
