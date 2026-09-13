"""FastAPI application factory — middleware, routers, lifespan."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.config import settings
from app.core.logging import configure_logging
from app.db.bootstrap import bring_database_up_to_date
from app.workloads.conversational.retention import run_retention_sweeps


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Migrate the schema and publish runbook versions BEFORE the first request is served.

    Deliberately blocking, and deliberately fatal on failure: an API answering against a schema it
    was not written for gives wrong answers instead of errors. See `app/db/bootstrap.py`.

    The retention sweep runs beside the app rather than before it: the practice promises callers
    that transcripts are deleted automatically after a fixed period (spec §4.1, §7), and a promise
    kept by the running service is one that cannot lapse because nobody installed a cron job.
    """
    await bring_database_up_to_date()
    retention = asyncio.create_task(run_retention_sweeps())
    try:
        yield
    finally:
        retention.cancel()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(
        lifespan=lifespan,
        title="Laufwise Platform — Control Plane",
        version=__version__,
        description="Governed agent runtime: run any agent inside an enforced, "
        "auditable process contract grounded in your systems of record.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()