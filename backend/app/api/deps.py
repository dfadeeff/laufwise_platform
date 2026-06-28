"""Shared FastAPI dependencies — constructed once, injected into routers."""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.control_plane.runtime import Runtime


@lru_cache
def get_runtime() -> Runtime:
    """Singleton Runtime facade over the control-plane engine."""
    return Runtime(runs_dir=settings.runs_dir)