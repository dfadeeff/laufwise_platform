"""Background execution of a calendar import (ADR-0004 D4).

`POST /import` returns immediately; the actual multi-round-trip migration runs here, off the
request, updating the `import_job` row after each appointment so the client can poll progress and
reconnect to a long run (imagine 500 appointments) without holding an HTTP connection open.

Why a thread, not an asyncio task: the calendar connectors use a SYNCHRONOUS httpx client, so a
30-minute job on the main event loop would freeze the whole server. `spawn_import_job` runs the
work in a dedicated thread with its own event loop + DB engine, fully isolating that blocking I/O.

This is the deliberately-simple staged-infra choice (PLATFORM_PLAN §6): durable progress in the
Postgres we already have, no queue/broker. Its one limitation is that a process restart mid-run
orphans the job as `running` — acceptable for a one-shot onboarding tool; the upgrade path when
concurrency demands it is a real task runner (Arq/Temporal) behind this same seam.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.control_plane.runtime import Runtime
from app.db import repo
from app.sync.orchestrator import ImportReport, run_import


async def execute_import_job(
    job_id: uuid.UUID, instance_id: uuid.UUID, tenant_id: uuid.UUID, window: dict
) -> None:
    """Run the import for `job_id` to completion, persisting progress as it goes. Owns its own DB
    engine/session (it runs outside any request) and never raises — a crash is recorded on the
    job as status=failed."""
    engine = create_async_engine(settings.sqlalchemy_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = await repo.get_import_job(session, job_id, tenant_id)
            instance = await repo.get_instance(session, instance_id, tenant_id)
            if job is None or instance is None:
                return

            async def on_progress(report: ImportReport) -> None:
                job.total = report.total
                job.created = list(report.created)
                job.skipped = list(report.skipped)
                job.failed = list(report.failed)
                job.excluded = list(report.excluded)
                job.forced = list(report.forced)
                await session.commit()

            try:
                runtime = Runtime(runs_dir=settings.runs_dir)
                await run_import(session, runtime, instance, window, on_progress=on_progress)
                job.status = "completed"
                await repo.finish_import_task(
                    session,
                    job,
                    status="completed",
                    summary={
                        "total": job.total,
                        "created": len(job.created or []),
                        "forced": len(job.forced or []),
                        "skipped": len(job.skipped or []),
                        "failed": len(job.failed or []),
                    },
                )
            except Exception as exc:  # noqa: BLE001 — any failure must land on the job, not vanish
                await session.rollback()
                job = await repo.get_import_job(session, job_id, tenant_id)
                if job is not None:
                    job.status = "failed"
                    job.error = str(exc)[:1000]
                    await repo.finish_import_task(
                        session,
                        job,
                        status="failed",
                        summary={"total": job.total},
                    )
            if job is not None:
                await session.commit()
    finally:
        await engine.dispose()


def spawn_import_job(
    job_id: uuid.UUID, instance_id: uuid.UUID, tenant_id: uuid.UUID, window: dict
) -> None:
    """Fire the import worker in a background thread (own event loop + engine). Returns at once so
    the request can respond `202`. This is the seam tests monkeypatch to run the worker inline."""

    def _worker() -> None:
        asyncio.run(execute_import_job(job_id, instance_id, tenant_id, window))

    threading.Thread(target=_worker, name=f"import-{job_id}", daemon=True).start()
