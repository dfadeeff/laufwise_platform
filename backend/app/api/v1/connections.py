"""Connection endpoints (ADR-0003) — a tenant connects a real system of record (thevea).

On create, the user's credentials are Fernet-encrypted before they touch the DB and never
returned. The configuration cockpit binds the resulting connection id to an instance's
`calendar` role at deploy time; the runtime decrypts it transiently to build the live client.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.connections import crypto
from app.connections.crypto import CredentialCryptoUnavailable
from app.db import repo
from app.db.models import Tenant
from app.db.session import get_session
from app.schemas.connection import ConnectionCreate, ConnectionSummary

router = APIRouter()


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

    conn = await repo.create_connection(
        session,
        tenant_id=tenant.id,
        type=req.type,
        adapter=req.adapter,
        tokens_enc=tokens_enc,
        config=req.config,
    )
    return ConnectionSummary.of(conn)