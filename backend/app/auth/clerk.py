"""Clerk session-token verification — the identity half of tenancy (ADR-0003 D2).

A signed-in user's Clerk session JWT (RS256) is verified against Clerk's JWKS. The token's
`org_id` claim is the active organization; org = tenant. Claims are signed, so the org id is
trustworthy — the client cannot forge which tenant it acts for.

Kept dependency-light: PyJWT's PyJWKClient fetches + caches the JWKS. The issuer/JWKS URL are
derived from the publishable key (config.clerk_issuer), so there is no separate auth config.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.config import settings


class InvalidToken(Exception):
    """Raised when a bearer token is missing, malformed, expired, or wrong-issuer."""


@dataclass(frozen=True)
class ClerkPrincipal:
    user_id: str
    org_id: str | None
    org_role: str | None
    org_slug: str | None


# One JWKS client per process; it caches signing keys and refreshes on rotation.
_jwks_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        issuer = settings.clerk_issuer
        if not issuer:
            raise InvalidToken("Clerk is not configured (no publishable key)")
        _jwks_client = PyJWKClient(f"{issuer}/.well-known/jwks.json")
    return _jwks_client


def verify_token(token: str) -> ClerkPrincipal:
    """Verify a Clerk session JWT and return its principal. Raises InvalidToken on any failure."""
    issuer = settings.clerk_issuer
    if not issuer:
        raise InvalidToken("Clerk is not configured (no publishable key)")
    try:
        signing_key = _client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},  # Clerk session tokens carry no fixed audience
        )
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise InvalidToken(str(exc)) from exc

    user_id = claims.get("sub")
    if not user_id:
        raise InvalidToken("token has no subject")
    # Clerk's default session token exposes org context flat (org_id/org_role/org_slug) or
    # nested under `o` in v2 tokens — accept either so token-template settings don't matter.
    org = claims.get("o") or {}
    return ClerkPrincipal(
        user_id=user_id,
        org_id=claims.get("org_id") or org.get("id"),
        org_role=claims.get("org_role") or org.get("rol"),
        org_slug=claims.get("org_slug") or org.get("slg"),
    )