"""A missing or misspelled deployment environment must never enable unsigned access."""

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import deps
from app.config import Settings, settings


def test_no_environment_means_authentication_is_required(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    configured = Settings(_env_file=None)
    monkeypatch.setattr(settings, "app_env", configured.app_env)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(deps.platform_operator(None))
    assert refused.value.status_code == 401
    with pytest.raises(HTTPException) as refused:
        deps.current_principal(None)
    assert refused.value.status_code == 401


def test_a_typo_in_the_environment_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prodution")


@pytest.mark.parametrize("environment", ["local", "test"])
def test_unsigned_development_is_an_explicit_environment(monkeypatch, environment):
    monkeypatch.setattr(settings, "app_env", environment)
    assert asyncio.run(deps.platform_operator(None)) == "local"
