"""Connection endpoints (ADR-0003) — a tenant connects a real system of record (thevea).

On create, the user's credentials are Fernet-encrypted before they touch the DB and never
returned. The configuration cockpit binds the resulting connection id to an instance's
`calendar` role at deploy time; the runtime decrypts it transiently to build the live client.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.config import settings
from app.connections import crypto
from app.connections.crypto import CredentialCryptoUnavailable
from app.connectors import base as connectors_base
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.connection import ConnectionCreate, ConnectionPreview, ConnectionSummary

router = APIRouter()


def _base_url_for(adapter: str, config: dict) -> str:
    return (config or {}).get("base_url") or (
        settings.healthyfeet_base_url if adapter == "healthyfeet" else settings.thevea_base_url
    )


def _verify_credentials(adapter: str, config: dict, credentials: dict[str, str]) -> None:
    """Prove the credentials actually authenticate against the real system BEFORE persisting, so a
    bad email/password is rejected in the connect form — not silently at import time. Built via the
    module attribute (`connectors_base.build_connector`) so tests can monkeypatch it."""
    connector = connectors_base.build_connector(
        adapter, _base_url_for(adapter, config), credentials
    )
    try:
        connector.verify()
    except Exception as exc:  # noqa: BLE001 — any auth/transport failure means a bad connection
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"could not connect to {adapter}: {exc}"
        ) from exc
    finally:
        connector.close()


@router.get("", response_model=list[ConnectionSummary])
async def list_connections(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[ConnectionSummary]:
    conns = await repo.list_connections(session, tenant.id)
    return [ConnectionSummary.of(c) for c in conns]


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
    # later import-time failure). "connected" then always means "these credentials work".
    _verify_credentials(req.adapter, req.config, req.credentials)

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
        conn.adapter, _base_url_for(conn.adapter, conn.config), creds
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