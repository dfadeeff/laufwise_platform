"""Two-step doctolib connect (Option A1), run by the schedule worker.

doctolib login can't be a single HTTP request: after username/password the browser lands on a
new-device email-code screen, and the code arrives by email *after* that submit — so the code
must come in a *second* request while the SAME headless browser stays alive on the code page.

So a login is a `doctolib_login` row, and the browser lives in the schedule worker, never in the
API process (a deploy of the API cannot cut a sign-in off, and any API instance can serve its
polls):

1. The API queues it (`start_login`): status `queued`, the password, PIN and kept browser state
   sealed with the connection key.
2. The worker takes it within a couple of seconds (`start_queued`) and runs the headless login in a
   thread. On the code screen it sets `awaiting_code` and waits for the code to appear on the row.
3. The API seals the emailed code onto the row (`submit_code`); the worker's browser types it.
4. The worker records the outcome (`done` with the sealed session, or `failed`); the API's next poll
   turns a `done` into the Connection (api/v1/connections._finalize_doctolib_login).

Every secret on the row is wiped once the login has its outcome, and a login nobody finished is
failed and wiped after `_TTL`. A browser does not survive its worker: a sign-in under way when the
worker stops is failed with "start again", never left hanging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.connections import crypto
from app.db import repo
from app.db.models import DoctolibLogin

log = logging.getLogger("connections")

_TTL = timedelta(minutes=20)  # a login nobody finished is failed and its secrets wiped
_WORKER_GONE = timedelta(minutes=2)  # a worker that has not ticked for this long is gone
_CODE_WAIT_S = 600  # how long the browser waits for the emailed code before giving up
_CODE_POLL_S = 2.0
MAX_LIVE_LOGINS = 2  # browsers one worker runs at once; each holds a Chromium
_RESTARTED = "the platform restarted during the sign-in; start it again"


@dataclass
class LoginJob:
    """A login as the API and the browser see it: the row, with its secrets opened."""

    id: str
    tenant_id: str  # the job (and the connection it creates) belong to this tenant only
    username: str
    agenda_ids: str
    status: str = "queued"  # queued | starting | awaiting_code | finishing | done | failed
    error: str | None = None
    # {_doctolib_session, pin_login, browser_state}: a live session, never in a repr or traceback.
    result: dict[str, str] | None = field(default=None, repr=False)
    connection_id: str | None = None  # set once the Connection row has been created from `result`
    # Signing in again: the connection this login refreshes in place, instead of creating one.
    refresh_connection_id: str | None = None
    # The login detail doctolib turned down ("password" | "pin"), so the studio asks for that one.
    refused: str | None = None
    # Non-secret settings stored on the Connection it creates: label and calendar mapping.
    config: dict = field(default_factory=dict)
    # Kept so the finished login can persist it on the Connection for autonomous per-import
    # re-login. Sealed on the row, never returned.
    password: str = field(default="", repr=False)
    # doctolib's device-trust cookie from an earlier connection of the same account. Presented to
    # the login so a reconnect from a device doctolib still trusts asks for no emailed code.
    pin_login: str = field(default="", repr=False)
    # The doctolib part of the browser from that same connection (cookies + site storage).
    browser_state: str = field(default="", repr=False)
    # The account's 4-digit PIN, typed when doctolib shows its PIN screen to a trusted device.
    pin: str = field(default="", repr=False)


# The sealed inputs of a login: the LoginJob fields kept out of every repr, except its result.
_SECRETS = tuple(f.name for f in fields(LoginJob) if not f.repr and f.name != "result")


def _seal(value: dict) -> str:
    return crypto.encrypt(json.dumps(value))


def _open(blob: str | None) -> dict | None:
    return json.loads(crypto.decrypt(blob)) if blob else None


def _forget(row: DoctolibLogin) -> None:
    row.secrets_enc = row.code_enc = row.result_enc = None


def _job(row: DoctolibLogin) -> LoginJob:
    secrets = _open(row.secrets_enc) or {}
    return LoginJob(
        id=row.id.hex, tenant_id=row.tenant_id.hex, username=row.username,
        agenda_ids=row.agenda_ids, status=row.status, error=row.error,
        result=_open(row.result_enc),
        connection_id=row.connection_id.hex if row.connection_id else None,
        refresh_connection_id=row.refresh_connection_id.hex if row.refresh_connection_id else None,
        refused=row.refused, config=dict(row.config or {}),
        **{k: str(secrets.get(k) or "") for k in _SECRETS},
    )


# --- the API's side --------------------------------------------------------------------------------


async def start_login(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    username: str,
    password: str,
    agenda_ids: str,
    config: dict | None = None,
    *,
    pin_login: str = "",
    browser_state: str = "",
    pin: str = "",
    refresh_connection_id: str | None = None,
) -> LoginJob:
    """Queue a login for the worker and commit it. Poll `load` until it is `awaiting_code` (needs
    a code), `done` or `failed`."""
    row = DoctolibLogin(
        tenant_id=tenant_id, status="queued", username=username, agenda_ids=agenda_ids,
        config=dict(config or {}),
        refresh_connection_id=uuid.UUID(refresh_connection_id) if refresh_connection_id else None,
        secrets_enc=_seal({"password": password, "pin_login": pin_login,
                           "browser_state": browser_state, "pin": pin}),
    )
    await repo.add_doctolib_login(session, row)
    await session.commit()
    return _job(row)


async def load(
    session: AsyncSession, job_id: str, tenant_id: uuid.UUID, *, lock: bool = False
) -> tuple[DoctolibLogin, LoginJob] | None:
    """The tenant's login `job_id`, as its row and its opened view; None for anyone else's."""
    try:
        login_id = uuid.UUID(job_id)
    except ValueError:
        return None
    row = await repo.get_doctolib_login(session, login_id, tenant_id, lock=lock)
    return (row, _job(row)) if row is not None else None


def keep(row: DoctolibLogin, job: LoginJob) -> None:
    """Write what finishing the login decided back onto its row. Once the connection exists, or
    the login has failed, nothing secret stays behind."""
    row.status, row.error, row.refused = job.status, job.error, job.refused
    row.connection_id = uuid.UUID(job.connection_id) if job.connection_id else None
    if job.status == "failed" or job.connection_id:
        _forget(row)


def submit_code(row: DoctolibLogin, code: str) -> bool:
    """Hand the emailed code to a login whose browser is waiting for it; False if none is."""
    if row.status != "awaiting_code":
        return False
    row.code_enc = crypto.encrypt(code)
    return True


# --- the worker's side -----------------------------------------------------------------------------


def run_login(job: LoginJob, get_code: Callable[[], str | None]) -> None:
    """Drive the headless login for `job` and record the outcome on it; never raises."""
    from app.providers.doctolib import DoctolibLoginRefused
    from app.providers.doctolib_login import headless_login

    try:
        job.result = headless_login(
            job.username, job.password, get_code,
            pin_login=job.pin_login, browser_state=job.browser_state, pin=job.pin,
        )
        job.status = "done"
    except DoctolibLoginRefused as exc:
        job.refused, job.error, job.status = exc.detail, str(exc), "failed"
    except Exception as exc:  # noqa: BLE001 — any failure lands on the login, never crashes the thread
        job.error, job.status = str(exc), "failed"


# The logins this worker's browsers are running: a stopping worker waits for them, then fails them.
LIVE_LOGINS: dict[uuid.UUID, threading.Thread] = {}


def _free_places() -> int:
    for done in [j for j, t in LIVE_LOGINS.items() if not t.is_alive()]:
        del LIVE_LOGINS[done]
    return MAX_LIVE_LOGINS - len(LIVE_LOGINS)


def _drive(login_id: uuid.UUID, node: str) -> None:
    """One login's browser, in its own thread, where Playwright's sync API runs. Playwright marks
    that thread as running an event loop, so the database steps go to a second thread that owns
    this login's own loop and engine, and the browser thread waits for each one."""
    loop = asyncio.new_event_loop()
    db_thread = threading.Thread(target=loop.run_forever, name=f"doctolib-db-{login_id}", daemon=True)
    db_thread.start()

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    engine = create_async_engine(settings.sqlalchemy_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        job = run(_held(sm, login_id, node, status=None))
        if job is None:
            return

        def get_code() -> str | None:
            # Called by headless_login only when the new-device code screen appears.
            if run(_held(sm, login_id, node, status="awaiting_code")) is None:
                return None
            deadline = time.monotonic() + _CODE_WAIT_S
            while time.monotonic() < deadline:
                code = run(_take_code(sm, login_id, node))
                if code is not None:
                    return code or None  # "" = the login is no longer this worker's
                time.sleep(_CODE_POLL_S)
            return None

        run_login(job, get_code)
        run(_record(sm, login_id, node, job))
    except Exception:  # noqa: BLE001 — the sweep fails it once its time is up
        log.exception("doctolib login %s", login_id)
    finally:
        run(engine.dispose())
        loop.call_soon_threadsafe(loop.stop)
        db_thread.join(5)
        loop.close()


async def _held(sm, login_id, node, *, status: str | None) -> LoginJob | None:
    async with sm() as session:
        row = await repo.held_doctolib_login(session, login_id, node)
        if row is None:
            return None
        if status is not None:
            row.status = status
            await session.commit()
        return _job(row)


async def _take_code(sm, login_id, node) -> str | None:
    """The code, once typed; None while there is none; "" when the login is no longer ours."""
    async with sm() as session:
        row = await repo.held_doctolib_login(session, login_id, node)
        if row is None:
            return ""
        if not row.code_enc:
            return None
        code = crypto.decrypt(row.code_enc)
        row.code_enc = None
        await session.commit()
        return code


async def _record(sm, login_id, node, job: LoginJob) -> None:
    async with sm() as session:
        row = await repo.held_doctolib_login(session, login_id, node)
        if row is None:
            return
        row.status, row.error, row.refused = job.status, job.error, job.refused
        row.code_enc = None
        if job.status == "done":
            row.result_enc = _seal(job.result or {})
        else:
            _forget(row)
        await session.commit()


async def start_queued(sm: async_sessionmaker, node: str) -> int:
    """Open a browser for each queued login this worker has room for."""
    async with sm() as session:
        rows = await repo.claim_queued_doctolib_logins(session, node, _free_places())
        ids = [row.id for row in rows]
        await session.commit()
    for login_id in ids:
        thread = threading.Thread(target=_drive, args=(login_id, node), name=f"doctolib-{login_id}", daemon=True)
        LIVE_LOGINS[login_id] = thread
        thread.start()
    return len(ids)


async def sweep(session: AsyncSession, now: datetime | None = None) -> None:
    """Fail and wipe what nobody finished in time: a login no worker took, a browser that died
    with its worker, a code never typed, a success whose poll never came."""
    now = now or datetime.now(timezone.utc)
    # A browser dies with its worker: once that worker stops ticking, nobody reads the code.
    for row in await repo.orphaned_doctolib_logins(session, now - _WORKER_GONE):
        row.status, row.error = "failed", _RESTARTED
        _forget(row)
    for row in await repo.unfinished_doctolib_logins(session, now - _TTL):
        if row.status != "failed" and not row.connection_id:
            row.status = "failed"
            row.error = row.error or "the sign-in did not finish in time; start it again"
        _forget(row)


async def drain(sm: async_sessionmaker, node: str, budget: float) -> None:
    """A stopping worker gives its browsers up to `budget` seconds (short: someone may be waiting
    on a code screen), then fails what is still signing in: a browser cannot move to the next
    worker, and the person can start again at once."""
    deadline = time.monotonic() + budget
    for thread in list(LIVE_LOGINS.values()):
        if (left := deadline - time.monotonic()) > 0:
            await asyncio.to_thread(thread.join, left)
    unfinished = [j for j, t in LIVE_LOGINS.items() if t.is_alive()]
    if unfinished:
        async with sm() as session:
            for login_id in unfinished:
                row = await repo.held_doctolib_login(session, login_id, node)
                if row is not None:
                    row.status, row.error = "failed", _RESTARTED
                    _forget(row)
            await session.commit()
