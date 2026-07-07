"""Shared FastAPI dependencies — constructed once, injected into routers."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.clerk import InvalidToken, verify_token
from app.config import settings
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session


@lru_cache
def get_runtime() -> Runtime:
    """Singleton Runtime facade over the control-plane engine."""
    return Runtime(runs_dir=settings.runs_dir)


async def current_tenant(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    """Resolve the calling tenant from the Clerk session token (org = tenant, ADR-0003 D2).

    A verified token's `org_id` claim selects the tenant — signed, so it can't be forged. With no
    token: rejected in production; outside production it falls back to the `default` tenant so
    local dev and the test suite (which send no token) keep working.
    """
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            principal = verify_token(token)
        except InvalidToken as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
        if not principal.org_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "no active organization — select or create one to continue",
            )
        return await repo.tenant_for_org(session, principal.org_id, name=principal.org_slug)

    if settings.app_env == "production":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return await repo.default_tenant(session)