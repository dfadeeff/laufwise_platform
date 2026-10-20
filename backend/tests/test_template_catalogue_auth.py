"""The shared template catalogue is written only by the platform's operators (red flag C0).

Every workspace deploys from it, so being signed in to one workspace is not enough to publish
into it — and before this, not even that was needed.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import deps
from app.auth.clerk import ClerkPrincipal
from app.config import settings
from app.main import app


def _principal(user_id: str) -> ClerkPrincipal:
    return ClerkPrincipal(user_id=user_id, org_id="org_1", org_role="org:admin", org_slug="x")


def _operator(authorization: str | None) -> str:
    return asyncio.run(deps.platform_operator(authorization))


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/templates"),
        ("get", "/api/v1/templates/voice_appointment"),
        ("post", "/api/v1/templates"),
        ("post", "/api/v1/templates/voice_appointment/publish"),
        ("get", "/api/v1/workflow-agents/workflows"),
        ("get", "/api/v1/workflow-agents"),
    ],
)
def test_the_catalogue_is_closed_to_an_unsigned_request(monkeypatch, method, path) -> None:
    monkeypatch.setattr(settings, "app_env", "production")

    response = getattr(TestClient(app), method)(path, **({"json": {}} if method == "post" else {}))

    assert response.status_code == 401


def test_a_workspace_member_who_is_not_an_operator_cannot_write_it(monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_admins", "user_operator")
    monkeypatch.setattr(deps, "verify_token", lambda token: _principal("user_member"))

    with pytest.raises(HTTPException) as refused:
        _operator("Bearer t")

    assert refused.value.status_code == 403


def test_an_operator_can_write_it(monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_admins", "user_a, user_operator")
    monkeypatch.setattr(deps, "verify_token", lambda token: _principal("user_operator"))

    assert _operator("Bearer t") == "user_operator"


def test_with_no_operators_configured_nobody_can(monkeypatch) -> None:
    monkeypatch.setattr(settings, "platform_admins", None)
    monkeypatch.setattr(deps, "verify_token", lambda token: _principal("user_operator"))

    with pytest.raises(HTTPException):
        _operator("Bearer t")
