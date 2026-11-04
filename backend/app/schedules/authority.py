"""Whether the person who created a schedule may still have it run (ADR-0040 D5).

A schedule runs on its creator's authority, so before every run the worker asks Clerk whether
that person is still an administrator of the workspace's organization. The answer is True, False
(no longer: the schedule is paused), or None (nobody could say: this run fails, the schedule
stays). Outside production, with no Clerk key, everyone is the local administrator, as every
request is (`app/api/deps.py`).
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from app.config import settings

log = logging.getLogger(__name__)
ADMIN_ROLES = ("org:admin", "admin")
PAGE = 100


async def still_admin(
    org_id: str | None, user_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> bool | None:
    if not settings.clerk_secret_key:
        return None if settings.app_env == "production" else True
    if not org_id or not user_id:
        return None
    url = f"{settings.clerk_api_url}/users/{quote(user_id, safe='')}/organization_memberships"
    headers = {"Authorization": f"Bearer {settings.clerk_secret_key}"}
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            offset = 0
            while True:  # every page: one missed membership would pause the schedule
                res = await client.get(url, params={"limit": PAGE, "offset": offset}, headers=headers)
                if res.status_code == 404:
                    return False  # the user no longer exists
                res.raise_for_status()
                body = res.json()
                memberships = body.get("data") or []
                for m in memberships:
                    if (m.get("organization") or {}).get("id") == org_id:
                        return m.get("role") in ADMIN_ROLES
                offset += len(memberships)
                if not memberships or offset >= int(body.get("total_count") or 0):
                    return False
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        log.warning("could not ask Clerk whether %s is an admin of %s: %s", user_id, org_id, exc)
        return None
