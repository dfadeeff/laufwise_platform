"""Execution of a calendar import or availability mirror (ADR-0004 D4), in the schedule worker.

`POST /import` and a schedule only queue an `import_job` with its window; the schedule worker
takes it (app/schedules/worker.py) and runs it here, updating the row after each appointment so
the Studio can poll progress of a long run (imagine 500 appointments). The API process never runs
one, so deploying the API cannot cut an import off.

Why a thread, not an asyncio task: the calendar connectors use a SYNCHRONOUS httpx client, so a
30-minute job on the worker's event loop would freeze its clock. `spawn_import_job` runs the work
in a dedicated thread with its own event loop + DB engine, fully isolating that blocking I/O.

A worker that stops or dies hands its jobs back to the queue and another worker runs them again
from the start, which the contract makes safe: what is already written is skipped. Every write is
fenced on `locked_by`, so a run whose job was handed on stops instead of racing its successor.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.control_plane.runtime import Runtime
from app.db import repo
from app.db.models import Template
from app.sync.block_mirror import run_block_mirror
from app.sync.mirror import run_mirror
from app.sync.orchestrator import ImportReport, run_import

# Which orchestrator drives a job, by the instance's template. Both walk a work-list and run one
# governed contract per item, so they share this worker, the job row and the report shape; what
# differs is the unit (an appointment vs. a day) and the direction (ADR-0009 D3).
_ORCHESTRATORS = {
    "calendar_import": run_import,
    "availability_mirror": run_mirror,
    "doctolib_block_mirror": run_block_mirror,
}

# The templates a schedule may run (app/schedules/targets.RunbookTarget). Anything else would be
# handed to the fallback below and run an IMPORT over a voice agent's parameters.
SCHEDULABLE_TEMPLATES = frozenset(_ORCHESTRATORS)


class _HandedOn(Exception):
    """This worker no longer holds the job: another one runs it now."""


async def execute_import_job(
    job_id: uuid.UUID, instance_id: uuid.UUID, tenant_id: uuid.UUID, window: dict, node: str
) -> None:
    """Run the import for `job_id` to completion, persisting progress as it goes, while worker
    `node` holds it. Owns its own DB engine/session (it runs outside any request) and never
    raises — a crash is recorded on the job as status=failed."""
    engine = create_async_engine(settings.sqlalchemy_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = await repo.get_import_job(session, job_id, tenant_id)
            instance = await repo.get_instance(session, instance_id, tenant_id)
            if job is None or instance is None or job.locked_by != node:
                return

            async def still_ours() -> None:
                if await repo.import_job_holder(session, job_id) != node:
                    raise _HandedOn

            async def on_progress(report: ImportReport) -> None:
                await still_ours()
                job.total = report.total
                job.created = list(report.created)
                job.skipped = list(report.skipped)
                job.failed = list(report.failed)
                job.excluded = list(report.excluded)
                job.forced = list(report.forced)
                job.review = list(report.review)
                job.patients = dict(report.patients)
                await session.commit()

            try:
                runtime = Runtime(runs_dir=settings.runs_dir)
                template = await session.get(Template, instance.template_id)
                orchestrate = _ORCHESTRATORS.get(getattr(template, "name", ""), run_import)
                await orchestrate(session, runtime, instance, window, on_progress=on_progress)
                await still_ours()
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
            except _HandedOn:
                await session.rollback()
                return
            except Exception as exc:  # noqa: BLE001 — any failure must land on the job, not vanish
                await session.rollback()
                job = await repo.get_import_job(session, job_id, tenant_id)
                if job is not None and job.status == "running" and job.locked_by == node:
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
    job_id: uuid.UUID, instance_id: uuid.UUID, tenant_id: uuid.UUID, window: dict, node: str
) -> threading.Thread:
    """Run a claimed job in a background thread (own event loop + engine), so the worker's clock
    keeps ticking while it runs."""

    def _worker() -> None:
        asyncio.run(execute_import_job(job_id, instance_id, tenant_id, window, node))

    thread = threading.Thread(target=_worker, name=f"import-{job_id}", daemon=True)
    thread.start()
    return thread
