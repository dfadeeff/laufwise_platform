"""Phone numbers a practice gets for itself: claimed from the platform's pool, wired up by us.

The pool is the platform Twilio account's voice numbers that no practice owns and that nothing else
uses. The operator buys numbers once, under one regulatory bundle; a practice claims one in the
Studio and forwards its existing line to it. That replaces the administrator editing
`VOICE_NUMBER_ASSIGNMENTS` and pointing the number's webhook in the Twilio console by hand.

Numbers already assigned through that variable keep working: they count as owned by their practice
and are never offered to anyone else.
"""

from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import repo
from app.workloads.conversational import telephony

# Enough for a practice with a second line or a second agent; few enough that one practice cannot
# empty the pool for everyone else.
MAX_NUMBERS_PER_PRACTICE = 3


class NumberError(Exception):
    """Something the practice can act on, said in its words. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class NumberTaken(Exception):
    """Another practice claimed the number between listing it and claiming it."""


# What Twilio puts on a number it has just sold: its own demo greeting. A factory default, not
# somebody's line.
_TWILIO_DEFAULT_VOICE_URL = "https://demo.twilio.com/welcome/voice/"


def poolable(row: dict, *, taken: set[str], webhook_url: str) -> bool:
    """Whether a number in the platform account may be offered to a practice.

    A number pointed at some other service is somebody's live line, not spare stock, so only an
    unset webhook, Twilio's own demo greeting, or our own counts as free.
    """
    voice_url = (row.get("voice_url") or "").strip()
    return (
        bool((row.get("capabilities") or {}).get("voice"))
        and row.get("phone_number") not in taken
        and voice_url in ("", webhook_url, _TWILIO_DEFAULT_VOICE_URL)
    )


def _credentials() -> tuple[str, str]:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise NumberError(
            "Phone service is not configured. Ask your administrator to configure Twilio.", 503
        )
    return settings.twilio_account_sid, settings.twilio_auth_token


async def _taken(session) -> dict[str, str]:
    """Every number a practice owns: claimed from the pool, or assigned before the pool existed."""
    return {**settings.voice_number_assignments, **await repo.phone_number_owners(session)}


async def owned(session, tenant_id) -> list[str]:
    tenant = str(tenant_id)
    legacy = [n for n, t in settings.voice_number_assignments.items() if t == tenant]
    claimed = await repo.phone_numbers_of(session, tenant_id)
    return list(dict.fromkeys([*claimed, *legacy]))


async def available(session, *, webhook_url: str, limit: int = 20) -> list[dict]:
    """Numbers a practice can claim right now, with Twilio's name for each."""
    account_sid, token = _credentials()
    taken = set(await _taken(session))
    rows = await telephony.account_numbers(account_sid, token)
    return [
        {"number": row["phone_number"], "name": row.get("friendly_name") or ""}
        for row in rows
        if poolable(row, taken=taken, webhook_url=webhook_url)
    ][:limit]


async def claim(session, tenant_id, number: str, *, webhook_url: str) -> None:
    """Make a pool number the practice's, and send its calls to us. The caller commits."""
    account_sid, token = _credentials()
    if len(await repo.phone_numbers_of(session, tenant_id)) >= MAX_NUMBERS_PER_PRACTICE:
        raise NumberError(
            f"A practice can hold at most {MAX_NUMBERS_PER_PRACTICE} numbers. Release one first."
        )
    taken = set(await _taken(session))
    row = next(
        (r for r in await telephony.account_numbers(account_sid, token) if r.get("phone_number") == number),
        None,
    )
    if row is None or not poolable(row, taken=taken, webhook_url=webhook_url):
        raise NumberError("That number is not available any more. Choose another.", 409)
    try:
        await repo.add_phone_number(
            session, number=number, tenant_id=tenant_id, twilio_sid=row["sid"]
        )
    except (IntegrityError, NumberTaken) as exc:
        raise NumberError("That number is not available any more. Choose another.", 409) from exc
    await telephony.point_voice_at(account_sid, token, row["sid"], webhook_url)


async def release(session, tenant_id, number: str) -> None:
    """Give a number back to the pool. Refused while an agent still holds it. The caller commits."""
    if (await repo.phone_number_owners(session)).get(number) != str(tenant_id):
        raise NumberError("That number is not yours to release.", 404)
    if await repo.phone_number_in_use(session, number):
        raise NumberError(
            "An agent still answers this number. Move the agent to another number first.", 409
        )
    await repo.remove_phone_number(session, number)


async def connect(session, tenant_id, number: str, *, webhook_url: str) -> None:
    """Before an agent answers a number: the practice owns it, it takes calls, and it reaches us."""
    if (await _taken(session)).get(number) != str(tenant_id):
        raise NumberError(
            "This number does not belong to your practice. Get a number in Phone & handoff first."
        )
    if not re.fullmatch(r"\+[1-9]\d{7,14}", number):
        raise NumberError("Enter an international number such as +493012345678.")
    account_sid, token = _credentials()
    row = next(
        (r for r in await telephony.account_numbers(account_sid, token) if r.get("phone_number") == number),
        None,
    )
    if row is None or not (row.get("capabilities") or {}).get("voice"):
        raise NumberError("This number is not a voice-enabled number in the platform's Twilio account.")
    await telephony.point_voice_at(account_sid, token, row["sid"], webhook_url)
