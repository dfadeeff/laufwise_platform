"""Shared FastAPI dependencies — constructed once, injected into routers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.clerk import InvalidToken, verify_token
from app.config import settings
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.kernel.modules import Module, installed, offers


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

async def platform_operator(authorization: str | None = Header(default=None)) -> str:
    """The signed-in user, if they may write what every workspace shares — the template catalogue
    (red flag C0). Membership of a workspace is not enough: a template published here is
    deployable by all of them. Outside production, an unsigned request acts as `local`, like
    `current_tenant`'s default tenant."""
    if authorization and authorization.lower().startswith("bearer "):
        try:
            principal = verify_token(authorization.split(" ", 1)[1].strip())
        except InvalidToken as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
        if principal.user_id not in settings.platform_admin_ids:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "only the platform's operators can change the shared template catalogue",
            )
        return principal.user_id
    if settings.app_env == "production":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return "local"


@dataclass(frozen=True)
class Principal:
    """The signed-in person, as the token says: who they are and their role in the workspace."""

    user_id: str
    role: str | None

    @property
    def is_admin(self) -> bool:
        return self.role in ("org:admin", "admin")


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """The person behind the request, from the same signed Clerk token as `current_tenant`
    (ADR-0035). Outside production a request without a token is a local administrator, as
    `current_tenant` falls back to the default tenant; in production it is refused."""
    if authorization and authorization.lower().startswith("bearer "):
        try:
            principal = verify_token(authorization.split(" ", 1)[1].strip())
        except InvalidToken as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {exc}") from exc
        return Principal(principal.user_id, principal.org_role)
    if settings.app_env == "production":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return Principal("local-dev", "org:admin")


async def workspace_modules(
    tenant: Tenant = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[Module]:
    """The installed modules the calling workspace runs (ADR-0028). Empty: it has not chosen."""
    modules = installed()
    keys = await repo.workspace_module_keys(session, tenant.id)
    return [modules[key] for key in keys if key in modules]


def requires_surface(surface: str):
    """A route dependency: the calling workspace runs a module offering `surface` (ADR-0028 D4).
    Red flag C9: the refusal belongs on every route that spends or acts for that surface, not
    only on the one that creates its agent."""

    async def check(running: list[Module] = Depends(workspace_modules)) -> None:
        if not offers(surface, running):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This workspace runs no module that offers voice agents."
                if surface == "voice_agents"
                else f"This workspace runs no module that offers {surface}.",
            )

    return Depends(check)
