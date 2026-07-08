"""Aggregates the v1 routers into a single APIRouter mounted by the app."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    approvals,
    connections,
    health,
    instances,
    runbooks,
    runs,
    templates,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(runbooks.router, prefix="/runbooks", tags=["runbooks"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
api_router.include_router(templates.router, prefix="/templates", tags=["templates"])
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])
api_router.include_router(connections.router, prefix="/connections", tags=["connections"])