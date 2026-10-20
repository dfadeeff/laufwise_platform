"""A workspace's service catalogue (ADR-0025) — HTTP only.

Tenant-scoped like every route: another workspace's service id is simply not found. Editing the
catalogue reaches an agent's calls only when the agent is published again (the snapshot pins it).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant, requires_surface
from app.db import repo
from app.db.session import get_session
from app.workloads.conversational import recognition

router = APIRouter()

LANGUAGES = ("de", "en", "ru")


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    # The calendar system's own id (Thevea: price-list position). Empty: not bookable by phone.
    external_id: str = Field(default="", max_length=80)
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    bookable: bool = True
    consultation: bool = False
    # Keys of the agent's payment categories (ADR-0027); empty means any.
    payment_categories: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("payment_categories")
    @classmethod
    def category_keys(cls, value):
        if any(not re.fullmatch(r"[a-z0-9_]{1,40}", k) for k in value):
            raise ValueError("Payment categories are given by their keys")
        return list(dict.fromkeys(value))

    @field_validator("aliases")
    @classmethod
    def known_languages(cls, value):
        unknown = set(value) - set(LANGUAGES)
        if unknown:
            raise ValueError(f"Aliases are for {', '.join(LANGUAGES)} only")
        return {
            lang: [w.strip() for w in words if w.strip()][:30] for lang, words in value.items()
        }


def _summary(row) -> dict:
    linked = bool((row.external_id or "").strip())
    return {
        "id": row.id.hex,
        "key": row.key,
        "name": row.name,
        "external_id": row.external_id or "",
        "duration_minutes": row.duration_minutes,
        "aliases": row.aliases or {},
        "bookable": row.bookable,
        "consultation": row.consultation,
        "payment_categories": list(row.payment_categories or []),
        # The release gate, said once here so the Studio never re-derives it.
        "phone_bookable": row.bookable and linked,
    }


def _key(name: str, taken: set[str]) -> str:
    """A stable, readable key from the name: "Hornhaut entfernen" → "hornhaut_entfernen"."""
    ascii_name = (
        unicodedata.normalize("NFKD", name.replace("ß", "ss")).encode("ascii", "ignore").decode()
    )
    base = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:70] or "service"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


async def _owned(session, tenant, service_id: str):
    try:
        parsed = uuid.UUID(service_id)
    except ValueError as exc:
        raise HTTPException(404, "No such service.") from exc
    row = await repo.get_service(session, parsed, tenant.id)
    if row is None:
        raise HTTPException(404, "No such service.")
    return row


@router.get("")
async def list_services(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> list[dict]:
    return [_summary(r) for r in await repo.list_services(session, tenant.id)]


@router.post("", dependencies=[requires_surface("voice_agents")])
async def add_service(
    req: ServiceIn, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    existing = await repo.list_services(session, tenant.id)
    row = await repo.add_service(
        session,
        tenant_id=tenant.id,
        key=_key(req.name, {r.key for r in existing}),
        position=len(existing),
        **req.model_dump(),
    )
    await session.commit()
    return _summary(row)


class Phrase(BaseModel):
    said: str = Field(min_length=1, max_length=300)


@router.post("/recognise", dependencies=[requires_surface("voice_agents")])
async def recognise(
    req: Phrase, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    """What a caller's words would match in today's catalogue — the Studio's "try a phrase".

    The same matching a call uses (ADR-0026), so a practice can check its phrases before it
    publishes them.
    """
    rows = await repo.list_services(session, tenant.id)
    keys = recognition.candidates(
        ((r.key, (r.name, *(w for said in (r.aliases or {}).values() for w in said))) for r in rows),
        req.said,
    )
    return {"matches": [_summary(r) for r in rows if r.key in keys]}


@router.put("/{service_id}", dependencies=[requires_surface("voice_agents")])
async def edit_service(
    service_id: str,
    req: ServiceIn,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    """The key stays as it was: published agents and past bookings name the service by it."""
    row = await _owned(session, tenant, service_id)
    for field, value in req.model_dump().items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _summary(row)


@router.delete("/{service_id}", dependencies=[requires_surface("voice_agents")])
async def delete_service(
    service_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    """A published agent keeps its pinned copy; the next publish no longer offers it."""
    row = await _owned(session, tenant, service_id)
    await session.delete(row)
    await session.commit()
    return {"deleted": row.id.hex}
