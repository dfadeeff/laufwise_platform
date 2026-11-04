"""A schedule runs on its creator's authority (ADR-0040 D5): before every run, Clerk is asked
whether that person is still an administrator of the workspace's organization."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import settings
from app.schedules.authority import still_admin

ORG = "org_hanna"


def _clerk(status: int, memberships: list[dict] | None = None):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"data": memberships or [], "total_count": len(memberships or [])})

    return httpx.MockTransport(handle), seen


def _ask(transport) -> bool | None:
    return asyncio.run(still_admin(ORG, "user_alice", transport=transport))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_x")


def test_an_admin_of_the_organization_may_still_run_it() -> None:
    transport, seen = _clerk(200, [{"role": "org:admin", "organization": {"id": ORG}}])
    assert _ask(transport) is True
    assert seen[0].url.path == "/v1/users/user_alice/organization_memberships"
    assert seen[0].headers["authorization"] == "Bearer sk_test_x"


@pytest.mark.parametrize(
    "memberships",
    [
        [{"role": "org:member", "organization": {"id": ORG}}],  # demoted
        [{"role": "org:admin", "organization": {"id": "org_other"}}],  # admin elsewhere
        [],  # left the organization
    ],
)
def test_someone_no_longer_an_admin_here_may_not(memberships) -> None:
    assert _ask(_clerk(200, memberships)[0]) is False


def test_a_deleted_user_may_not() -> None:
    assert _ask(_clerk(404)[0]) is False


def test_when_clerk_cannot_answer_nobody_knows() -> None:
    assert _ask(_clerk(500)[0]) is None

    def down(request):
        raise httpx.ConnectError("unreachable")

    assert _ask(httpx.MockTransport(down)) is None


def test_without_a_key_production_refuses_and_local_development_allows(monkeypatch) -> None:
    monkeypatch.setattr(settings, "clerk_secret_key", None)
    monkeypatch.setattr(settings, "app_env", "production")
    assert _ask(None) is None
    monkeypatch.setattr(settings, "app_env", "development")
    assert _ask(None) is True


def test_a_workspace_without_an_organization_cannot_be_confirmed() -> None:
    transport, _ = _clerk(200, [{"role": "org:admin", "organization": {"id": ORG}}])
    assert asyncio.run(still_admin(None, "user_alice", transport=transport)) is None
