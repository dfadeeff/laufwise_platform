"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.observability.trace import otlp_configured

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready() -> dict[str, object]:
    """Readiness: reports which optional integrations are wired."""
    return {"status": "ready", "otlp_tracing": otlp_configured()}