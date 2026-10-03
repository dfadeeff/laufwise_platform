"""A workspace's documents for its agents (ADR-0017) — HTTP only.

Tenant-scoped like every route: a document id from another workspace is simply not found. The
rules (extraction, limits, what reaches the prompt) live in `app.agents.knowledge`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import knowledge, service, web_import
from app.api.deps import current_tenant
from app.db import repo
from app.db.session import get_session

router = APIRouter()


class TextDocument(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class WebPage(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    # Defaults to the page's own title.
    title: str = Field(default="", max_length=200)


class PdfDocument(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # The file, base64-encoded by the Studio: no multipart dependency for one upload form.
    data_base64: str = Field(min_length=1)


def _summary(document) -> dict:
    return {
        "id": document.id.hex,
        "title": document.title,
        "source": document.source,
        "chars": len(document.content),
        "updated_at": document.updated_at,
    }


async def _owned(session, tenant, document_id: str):
    try:
        parsed = uuid.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(404, "No such document.") from exc
    document = await repo.get_knowledge(session, parsed, tenant.id)
    if document is None:
        raise HTTPException(404, "No such document.")
    return document


@router.get("")
async def list_documents(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    documents = await repo.list_knowledge(session, tenant.id)
    return {
        "documents": [_summary(d) for d in documents],
        "max_agent_chars": knowledge.MAX_AGENT_CHARS,
    }


@router.post("")
async def add_text(
    req: TextDocument, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    try:
        content = knowledge.checked(req.content)
    except knowledge.KnowledgeError as exc:
        raise HTTPException(422, str(exc)) from exc
    document = await repo.add_knowledge(
        session, tenant_id=tenant.id, title=req.title.strip(), source="text", content=content
    )
    await session.commit()
    return _summary(document)


@router.post("/pdf")
async def add_pdf(
    req: PdfDocument, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    try:
        content = knowledge.checked(knowledge.decode_upload(req.data_base64))
    except knowledge.KnowledgeError as exc:
        raise HTTPException(422, str(exc)) from exc
    document = await repo.add_knowledge(
        session, tenant_id=tenant.id, title=req.title.strip(), source="pdf", content=content
    )
    await session.commit()
    return _summary(document)


async def _fetched(url: str) -> str:
    try:
        return await asyncio.to_thread(web_import.fetch, url)
    except web_import.WebImportError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/url")
async def add_web_page(
    req: WebPage, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    """A page of the practice's website, as a document the practice can then review and edit."""
    title, text = web_import.page_text(await _fetched(req.url))
    try:
        content = knowledge.checked(f"Source: {req.url.strip()}\n\n{text}")
    except knowledge.KnowledgeError as exc:
        raise HTTPException(422, str(exc)) from exc
    document = await repo.add_knowledge(
        session,
        tenant_id=tenant.id,
        title=(req.title.strip() or title or req.url.strip())[:200],
        source="url",
        content=content,
    )
    await session.commit()
    return _summary(document)


@router.post("/prices")
async def propose_prices(req: WebPage, tenant=Depends(current_tenant)) -> dict:
    """Treatments and prices a price page names. Proposals only: nothing is saved here, and none
    reach an agent until the practice confirms them in its Treatments."""
    proposals = web_import.price_proposals(await _fetched(req.url))
    if not proposals:
        raise HTTPException(422, "No prices were found on that page.")
    return {"url": req.url.strip(), "treatments": proposals}


@router.get("/{document_id}")
async def read_document(
    document_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    document = await _owned(session, tenant, document_id)
    return {**_summary(document), "content": document.content}


@router.put("/{document_id}")
async def edit_document(
    document_id: str,
    req: TextDocument,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    """Edits reach an agent's calls only when it is published again (ADR-0017)."""
    document = await _owned(session, tenant, document_id)
    try:
        document.content = knowledge.checked(req.content)
    except knowledge.KnowledgeError as exc:
        raise HTTPException(422, str(exc)) from exc
    document.title = req.title.strip()
    document.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {**_summary(document), "content": document.content}


@router.delete("/{document_id}")
async def delete_document(
    document_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    """Refused while an agent's draft still knows it. A published agent keeps its pinned copy."""
    document = await _owned(session, tenant, document_id)
    users = await service.agents_using_document(session, tenant.id, document.id.hex)
    if users:
        raise HTTPException(
            409, f"{', '.join(users)} still uses this document. Remove it there first."
        )
    await session.delete(document)
    await session.commit()
    return {"deleted": document.id.hex}
