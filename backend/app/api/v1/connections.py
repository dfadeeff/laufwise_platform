"""Connection endpoints (ADR-0003) — a tenant connects a real system of record (thevea).

On create, the user's credentials are Fernet-encrypted before they touch the DB and never
returned. The configuration cockpit binds the resulting connection id to an instance's
`calendar` role at deploy time; the runtime decrypts it transiently to build the live client.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.config import settings
from app.connections import crypto, doctolib_login_jobs
from app.connections.crypto import CredentialCryptoUnavailable
from app.connectors import base as connectors_base
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.connection import (
    ConnectionCreate,
    ConnectionPreview,
    ConnectionSummary,
    DoctolibCodeSubmit,
    DoctolibLoginStart,
    DoctolibLoginStatus,
)

router = APIRouter()


# Per-adapter default base URL (a connection's config may override with its own `base_url`).
_ADAPTER_BASE_URL = {
    "thevea": lambda: settings.thevea_base_url,
    "healthyfeet": lambda: settings.healthyfeet_base_url,
    "doctolib": lambda: settings.doctolib_base_url,
}


def _base_url_for(adapter: str, config: dict) -> str:
    default = _ADAPTER_BASE_URL.get(adapter)
    return (config or {}).get("base_url") or (default() if default else settings.thevea_base_url)


def _adapter_opts(adapter: str, config: dict | None) -> dict:
    """Non-secret connector kwargs from a connection's config. doctolib needs its agenda ids so
    connect-time verify() and preview actually read the agenda (not an empty set); others take
    none. Mirrors the source wiring in app.connections.resolve."""
    if adapter == "doctolib":
        return {"agenda_ids": (config or {}).get("agenda_ids", "")}
    return {}


# The connect request must return well under the frontend's request timeout (12s). The system's
# login is a blocking network call of unknown latency from the server's region, so we run it OFF
# the event loop and cap it. We reject only on an AFFIRMATIVE, fast credential rejection; a
# timeout means we couldn't get a verdict in time — allow the connection rather than block the
# user (the import re-validates against real state anyway, ADR-0004).
_VERIFY_BUDGET_S = 8.0


async def _verify_credentials(adapter: str, config: dict, credentials: dict[str, str]) -> None:
    """Best-effort connect-time credential check. Runs the connector's blocking `verify()` in a
    thread with a budget under the frontend timeout: a fast rejection -> 400 (bad email/password);
    a timeout -> allow (a slow/unreachable system must not hang the connect form). Built via the
    module attribute (`connectors_base.build_connector`) so tests can monkeypatch it."""
    connector = connectors_base.build_connector(
        adapter, _base_url_for(adapter, config), credentials, **_adapter_opts(adapter, config)
    )
    try:
        await asyncio.wait_for(asyncio.to_thread(connector.verify), timeout=_VERIFY_BUDGET_S)
    except asyncio.TimeoutError:
        return  # no verdict in time — don't block; import will surface any real problem
    except Exception as exc:  # noqa: BLE001 — the system responded rejecting the credentials
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"could not connect to {adapter}: {exc}"
        ) from exc
    finally:
        try:
            connector.close()
        except Exception:  # noqa: BLE001 — a lingering verify thread may still hold the client
            pass


@router.get("", response_model=list[ConnectionSummary])
async def list_connections(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[ConnectionSummary]:
    conns = await repo.list_connections(session, tenant.id)
    return [ConnectionSummary.of(c) for c in conns]


# --- doctolib two-step connect (Option A1) -----------------------------------------------------
# doctolib's login can't be one request: the email code arrives AFTER the password submit, and the
# same headless browser must stay alive on the code screen to receive it. So: start a server-side
# login job, poll it, deliver the code — and create the Connection only once the login succeeds.

def _login_status(job: doctolib_login_jobs.LoginJob) -> DoctolibLoginStatus:
    return DoctolibLoginStatus(
        job_id=job.id, status=job.status, error=job.error, connection_id=job.connection_id
    )


def _owned_job(job_id: str, tenant: Tenant) -> doctolib_login_jobs.LoginJob:
    """Fetch a login job, enforcing tenant isolation (a random job id is not, alone, authorization)."""
    job = doctolib_login_jobs.get_job(job_id)
    if job is None or job.tenant_id != tenant.id.hex:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no login job {job_id}")
    return job


async def _finalize_doctolib_login(session: AsyncSession, tenant: Tenant, job) -> None:
    """On success, create the Connection exactly once — storing username/password + pin_login (for
    autonomous per-import re-login) and the fresh session (for an import run right after connect)."""
    if job.status != "done" or job.result is None or job.connection_id is not None:
        return
    creds = {
        "username": job.username,
        "password": job.password,
        "session_cookie": job.result.get("_doctolib_session", ""),
        "pin_login": job.result.get("pin_login", ""),
    }
    # Prove the captured session actually authenticates BEFORE storing it. The headless login can
    # report success and hand back a session doctolib does not accept (see doctolib_login.py's
    # verification status) — stored unchecked, that reads as "connected" in the studio and fails as
    # a 401 on the first import, which sends the operator to reconnect an account that will keep
    # failing the same way. Failing the job here says so while they are still on the connect form.
    try:
        await _verify_credentials("doctolib", {"agenda_ids": job.agenda_ids}, creds)
    except HTTPException as exc:
        job.status = "failed"
        job.error = (
            "the login completed but the captured session does not authenticate "
            f"({exc.detail}). Paste a session cookie from a signed-in browser instead."
        )
        return
    conn = await repo.create_connection(
        session,
        tenant_id=tenant.id,
        type="calendar",
        adapter="doctolib",
        tokens_enc=crypto.encrypt(json.dumps(creds)),
        config={"agenda_ids": job.agenda_ids},
    )
    job.connection_id = conn.id.hex if isinstance(conn.id, uuid.UUID) else str(conn.id)


@router.post("/doctolib/login", response_model=DoctolibLoginStatus)
async def start_doctolib_login(
    req: DoctolibLoginStart,
    tenant: Tenant = Depends(current_tenant),
) -> DoctolibLoginStatus:
    """Start a headless doctolib login. Returns immediately; poll GET /doctolib/login/{job_id}
    until `awaiting_code` (then POST the emailed code) or `done`/`failed`."""
    try:
        crypto.encrypt("probe")  # fail fast if no encryption key (same guard as create_connection)
    except CredentialCryptoUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    job = doctolib_login_jobs.start_login(tenant.id.hex, req.username, req.password, req.agenda_ids)
    return _login_status(job)


@router.get("/doctolib/login/{job_id}", response_model=DoctolibLoginStatus)
async def poll_doctolib_login(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> DoctolibLoginStatus:
    job = _owned_job(job_id, tenant)
    await _finalize_doctolib_login(session, tenant, job)  # create the connection once, on success
    return _login_status(job)


@router.post("/doctolib/login/{job_id}/code", response_model=DoctolibLoginStatus)
async def submit_doctolib_code(
    job_id: str,
    req: DoctolibCodeSubmit,
    tenant: Tenant = Depends(current_tenant),
) -> DoctolibLoginStatus:
    job = _owned_job(job_id, tenant)
    doctolib_login_jobs.submit_code(job_id, req.code)
    return _login_status(job)


@router.post("", response_model=ConnectionSummary)
async def create_connection(
    req: ConnectionCreate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ConnectionSummary:
    try:
        tokens_enc = crypto.encrypt(json.dumps(req.credentials))
    except CredentialCryptoUnavailable as exc:
        # No CONNECTION_ENC_KEY configured — refuse rather than store credentials in the clear.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    # Reject credentials that don't actually log in, before anything is stored (400, not a
    # later import-time failure) — best-effort, time-boxed so a slow system can't hang connect.
    await _verify_credentials(req.adapter, req.config, req.credentials)

    conn = await repo.create_connection(
        session,
        tenant_id=tenant.id,
        type=req.type,
        adapter=req.adapter,
        tokens_enc=tokens_enc,
        config=req.config,
    )
    return ConnectionSummary.of(conn)


@router.post("/{connection_id}/preview", response_model=ConnectionPreview)
async def preview_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> ConnectionPreview:
    """Read-only peek at a source connection: build the connector, fetch the raw calendar, and
    return a small sample. No writes — proves the agent can read, and reveals the JSON shape."""
    try:
        cid = uuid.UUID(connection_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid connection id") from exc
    conn = await repo.get_connection(session, cid, tenant.id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no connection {connection_id}")

    creds = json.loads(crypto.decrypt(conn.tokens_enc)) if conn.tokens_enc else {}
    connector = connectors_base.build_connector(
        conn.adapter,
        _base_url_for(conn.adapter, conn.config),
        creds,
        **_adapter_opts(conn.adapter, conn.config),
    )
    try:
        # The parsed appointments the agent will import (read-only). Sample the first few.
        if hasattr(connector, "list_appointments"):
            appts = connector.list_appointments({})
            if appts:
                return ConnectionPreview(ok=True, count=len(appts), raw=[a.raw for a in appts[:5]])
        # Nothing parsed — fall back to the diagnostic probe so we can see why.
        if hasattr(connector, "probe"):
            results = connector.probe()
            return ConnectionPreview(ok=True, count=0, raw=results)
        return ConnectionPreview(ok=False, error="preview is only supported for a source connection")
    except Exception as exc:  # noqa: BLE001 — a read failure is reported to the UI, never a 500
        return ConnectionPreview(ok=False, error=str(exc))
    finally:
        connector.close()