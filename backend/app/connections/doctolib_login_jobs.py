"""Two-step doctolib connect (Option A1), as an in-process job registry.

doctolib login can't be a single HTTP request: after username/password the browser lands on a
new-device email-code screen, and the code arrives by email *after* that submit — so the code
must come in a *second* request while the SAME headless browser stays alive on the code page.

So: `start_login` spawns a thread that runs the headless login and, when it reaches the code page,
parks on a threading.Event (status -> awaiting_code). `submit_code` sets the event; the thread
enters the code and finishes (status -> done, holding the captured session + pin_login). The
Connection row is created by the endpoint only on success, so there is NO DB schema change.

Same deliberately-simple in-process model as the import jobs (single Railway replica): state lives
in a module dict, jobs are pruned after a TTL. Playwright's SYNC API runs fine in these plain
worker threads (not on the event loop).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

_TTL_S = 1200  # abandoned jobs are pruned after 20 min
_CODE_WAIT_S = 600  # how long the login thread waits for the code before giving up


@dataclass
class LoginJob:
    id: str
    tenant_id: str  # the job (and the connection it creates) belong to this tenant only
    username: str
    agenda_ids: str
    status: str = "starting"  # starting | awaiting_code | done | failed
    error: str | None = None
    result: dict[str, str] | None = None  # {_doctolib_session, pin_login} on success
    connection_id: str | None = None  # set once the Connection row has been created from `result`
    created_at: float = field(default_factory=time.time)
    # Held in memory (never serialized/returned) so the code-submit endpoint can persist it on the
    # Connection for autonomous per-import re-login. Pruned with the job.
    password: str = field(default="", repr=False)
    _code_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _code: str | None = field(default=None, repr=False)


_jobs: dict[str, LoginJob] = {}
_lock = threading.Lock()


def _prune() -> None:
    now = time.time()
    for jid in [j for j, v in _jobs.items() if now - v.created_at > _TTL_S]:
        _jobs.pop(jid, None)


def start_login(tenant_id: str, username: str, password: str, agenda_ids: str) -> LoginJob:
    """Kick off a headless login in a worker thread. Returns immediately with a job in `starting`;
    poll `get_job` until it reaches `awaiting_code` (needs a code), `done`, or `failed`."""
    job = LoginJob(
        id=uuid.uuid4().hex, tenant_id=tenant_id, username=username,
        agenda_ids=agenda_ids, password=password,
    )
    with _lock:
        _prune()
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def get_job(job_id: str) -> LoginJob | None:
    with _lock:
        return _jobs.get(job_id)


def submit_code(job_id: str, code: str) -> LoginJob | None:
    """Hand the emailed code to a job that's awaiting it, and unblock its login thread."""
    job = get_job(job_id)
    if job is not None and job.status == "awaiting_code":
        job._code = code
        job._code_event.set()
    return job


def _run(job: LoginJob) -> None:
    from app.providers.doctolib_login import headless_login

    def get_code() -> str | None:
        # Called by headless_login only when the new-device code screen appears.
        job.status = "awaiting_code"
        if job._code_event.wait(timeout=_CODE_WAIT_S):
            return job._code
        return None

    try:
        job.result = headless_login(job.username, job.password, get_code)
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 — any failure lands on the job, never crashes the thread
        job.status = "failed"
        job.error = str(exc)
