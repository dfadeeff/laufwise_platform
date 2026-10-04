"""A practice gets its phone number in the Studio: claimed from the platform's pool, wired up itself.

The pool is the platform Twilio account's voice numbers that no practice owns and nothing else uses.
Claiming one makes it the practice's and points its webhook at the agent, so nobody configures the
Twilio console by hand. No network: Twilio's REST API is `httpx.MockTransport` or a stub.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.workloads.conversational import telephony

WEBHOOK = "https://api.example.com/api/v1/telephony/incoming"


def _row(number: str, *, voice: bool = True, voice_url: str = "", sid: str | None = None) -> dict:
    return {
        "phone_number": number,
        "sid": sid or "PN" + number[-6:],
        "friendly_name": f"Munich {number[-4:]}",
        "capabilities": {"voice": voice},
        "voice_url": voice_url,
    }


# --- talking to Twilio ------------------------------------------------------------------------


def test_the_platform_account_s_numbers_are_listed_from_twilio() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2010-04-01/Accounts/AC1/IncomingPhoneNumbers.json"
        return httpx.Response(200, json={"incoming_phone_numbers": [_row("+498912345678")]})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await telephony.account_numbers("AC1", "secret", client=client)

    assert [row["phone_number"] for row in asyncio.run(main())] == ["+498912345678"]


def test_a_number_is_pointed_at_the_agent_without_the_twilio_console() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await telephony.point_voice_at("AC1", "secret", "PN1", WEBHOOK, client=client)

    asyncio.run(main())

    assert seen["path"] == "/2010-04-01/Accounts/AC1/IncomingPhoneNumbers/PN1.json"
    assert seen["body"] == {"VoiceUrl": [WEBHOOK], "VoiceMethod": ["POST"]}


# --- what counts as the pool ----------------------------------------------------------------


def test_the_pool_offers_only_numbers_nobody_owns_and_nothing_else_uses() -> None:
    """A number already pointed at another service is somebody's live line, not spare stock."""
    from app.agents.numbers import poolable

    rows = [
        _row("+491"),  # free
        _row("+492", voice_url=WEBHOOK),  # free, already ours
        _row("+493"),  # claimed by a practice
        _row("+494", voice=False),  # cannot take calls
        _row("+495", voice_url="https://elsewhere.example/voice"),  # another service's line
    ]

    offered = [r["phone_number"] for r in rows if poolable(r, taken={"+493"}, webhook_url=WEBHOOK)]

    assert offered == ["+491", "+492"]


# --- claiming and releasing -----------------------------------------------------------------


class _Store:
    """The numbers table, in memory."""

    def __init__(self, owned: dict[str, str] | None = None, in_use: set[str] | None = None):
        self.owned = dict(owned or {})
        self.in_use = set(in_use or ())


@pytest.fixture
def platform(monkeypatch):
    """Twilio with three numbers, a numbers table, and a record of every webhook pointed."""
    from app.agents import numbers

    store = _Store()
    pointed: list[tuple[str, str]] = []

    async def account_numbers(*_args, **_kwargs):
        return [_row("+498910000100"), _row("+498910000200"), _row("+498910000300")]

    async def point_voice_at(_sid, _token, number_sid, url, **_kwargs):
        pointed.append((number_sid, url))

    async def owners(_session):
        return dict(store.owned)

    async def owned_by(_session, tenant_id):
        return [n for n, t in store.owned.items() if t == tenant_id]

    async def add(_session, *, number, tenant_id, twilio_sid):
        if number in store.owned:
            raise numbers.NumberTaken(number)
        store.owned[number] = tenant_id

    async def remove(_session, number):
        store.owned.pop(number, None)

    async def in_use(_session, number):
        return number in store.in_use

    monkeypatch.setattr(numbers.settings, "twilio_account_sid", "AC1")
    monkeypatch.setattr(numbers.settings, "twilio_auth_token", "secret")
    monkeypatch.setattr(numbers.settings, "voice_number_assignments", {})
    monkeypatch.setattr(numbers.telephony, "account_numbers", account_numbers)
    monkeypatch.setattr(numbers.telephony, "point_voice_at", point_voice_at)
    monkeypatch.setattr(numbers.repo, "phone_number_owners", owners)
    monkeypatch.setattr(numbers.repo, "phone_numbers_of", owned_by)
    monkeypatch.setattr(numbers.repo, "add_phone_number", add)
    monkeypatch.setattr(numbers.repo, "remove_phone_number", remove)
    monkeypatch.setattr(numbers.repo, "phone_number_in_use", in_use)
    return numbers, store, pointed


def test_a_claimed_number_becomes_the_practice_s_and_answers_its_agent(platform) -> None:
    numbers, store, pointed = platform

    asyncio.run(numbers.claim(None, "practice-a", "+498910000200", webhook_url=WEBHOOK))

    assert store.owned == {"+498910000200": "practice-a"}
    assert pointed == [("PN000200", WEBHOOK)]
    available = asyncio.run(numbers.available(None, webhook_url=WEBHOOK))
    assert [n["number"] for n in available] == ["+498910000100", "+498910000300"]


def test_a_number_another_practice_owns_cannot_be_claimed(platform) -> None:
    numbers, store, _ = platform
    store.owned["+498910000200"] = "practice-b"

    with pytest.raises(numbers.NumberError, match="not available"):
        asyncio.run(numbers.claim(None, "practice-a", "+498910000200", webhook_url=WEBHOOK))


def test_a_number_outside_the_platform_account_cannot_be_claimed(platform) -> None:
    numbers, _store, _ = platform

    with pytest.raises(numbers.NumberError, match="not available"):
        asyncio.run(numbers.claim(None, "practice-a", "+4930999", webhook_url=WEBHOOK))


def test_one_practice_cannot_empty_the_pool(platform, monkeypatch) -> None:
    numbers, _store, _ = platform
    monkeypatch.setattr(numbers, "MAX_NUMBERS_PER_PRACTICE", 1)
    asyncio.run(numbers.claim(None, "practice-a", "+498910000100", webhook_url=WEBHOOK))

    with pytest.raises(numbers.NumberError, match="at most 1"):
        asyncio.run(numbers.claim(None, "practice-a", "+498910000200", webhook_url=WEBHOOK))


def test_a_number_an_agent_still_answers_cannot_be_released(platform) -> None:
    """Releasing a live line would leave callers hearing "this number is not available"."""
    numbers, store, _ = platform
    store.owned["+498910000100"] = "practice-a"
    store.in_use.add("+498910000100")

    with pytest.raises(numbers.NumberError, match="agent"):
        asyncio.run(numbers.release(None, "practice-a", "+498910000100"))

    store.in_use.clear()
    asyncio.run(numbers.release(None, "practice-a", "+498910000100"))
    assert store.owned == {}


def test_a_practice_cannot_release_another_practice_s_number(platform) -> None:
    numbers, store, _ = platform
    store.owned["+498910000100"] = "practice-b"

    with pytest.raises(numbers.NumberError, match="not yours"):
        asyncio.run(numbers.release(None, "practice-a", "+498910000100"))
    assert store.owned == {"+498910000100": "practice-b"}


# --- activation -----------------------------------------------------------------------------


def test_activation_takes_a_number_the_practice_owns_and_wires_it_up(platform) -> None:
    numbers, store, pointed = platform
    store.owned["+498910000300"] = "practice-a"

    asyncio.run(numbers.connect(None, "practice-a", "+498910000300", webhook_url=WEBHOOK))

    assert pointed == [("PN000300", WEBHOOK)]


def test_activation_still_honours_a_number_an_administrator_assigned(platform, monkeypatch) -> None:
    """Numbers assigned before the pool existed keep working; nothing in production breaks."""
    numbers, _store, pointed = platform
    monkeypatch.setattr(numbers.settings, "voice_number_assignments", {"+498910000100": "practice-a"})

    asyncio.run(numbers.connect(None, "practice-a", "+498910000100", webhook_url=WEBHOOK))

    assert pointed == [("PN000100", WEBHOOK)]
    assert "+498910000100" not in [n["number"] for n in asyncio.run(numbers.available(None, webhook_url=WEBHOOK))]


def test_activation_refuses_a_number_the_practice_does_not_own(platform) -> None:
    numbers, store, pointed = platform
    store.owned["+498910000300"] = "practice-b"

    with pytest.raises(numbers.NumberError, match="Get a number"):
        asyncio.run(numbers.connect(None, "practice-a", "+498910000300", webhook_url=WEBHOOK))
    assert pointed == []


def test_a_fresh_number_still_on_twilio_s_demo_answer_is_free_stock() -> None:
    """Twilio gives a new number its demo greeting. That is a factory default, not somebody's
    line, and treating it as one hid a freshly bought number from the pool."""
    from app.agents.numbers import poolable

    row = _row("+16503628764", voice_url="https://demo.twilio.com/welcome/voice/")

    assert poolable(row, taken=set(), webhook_url=WEBHOOK)
