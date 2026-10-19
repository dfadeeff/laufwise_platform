"""A workspace's files (ADR-0029) — HTTP only.

An upload is the raw file as the request body, its name in the query: no form encoding and no
new dependency. The body is read with a cap, so an oversized upload is refused while it streams.
A download is always an attachment with sniffing off, so a stored file is never rendered as a page
of this origin.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant
from app.db import repo
from app.db.models import StoredFile
from app.db.session import get_session
from app.files.content import MAX_FILE_BYTES
from app.files.service import FileRejected, remove_file, store_file
from app.files.store import FileStoreUnavailable, blob_store

router = APIRouter()


def _summary(row: StoredFile) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _store():
    try:
        return blob_store()
    except FileStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "the file is too large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "the file is too large")
    return bytes(data)


async def _owned(session: AsyncSession, file_id: str, tenant, *, lock: bool = False) -> StoredFile:
    try:
        parsed = uuid.UUID(file_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file") from exc
    row = await repo.get_stored_file(session, parsed, tenant.id, lock=lock)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    return row


@router.get("")
async def list_files(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> list[dict]:
    return [_summary(f) for f in await repo.list_stored_files(session, tenant.id)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    response: Response,
    name: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    data = await _body(request)
    try:
        row, created = await store_file(session, _store(), tenant.id, name, data)
    except FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except FileStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return _summary(row)


@router.get("/{file_id}")
async def get_file(
    file_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    return _summary(await _owned(session, file_id, tenant))


@router.get("/{file_id}/content")
async def download_file(
    file_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> Response:
    row = await _owned(session, file_id, tenant)
    try:
        data = await _store().get(row.storage_key)
    except FileStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return Response(
        content=data,
        media_type=row.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{file_id}")
async def delete_file(
    file_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    # Locked until the delete commits: an attach in between waits, then finds the file gone,
    # instead of linking a case to a blob that is already deleted (red flag C8).
    row = await _owned(session, file_id, tenant, lock=True)
    if await repo.file_attached_to_case(session, row.id):
        # A case was, or will be, computed from it: deleting it would take its evidence away.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this file belongs to a case; detach it from the case first"
        )
    try:
        await remove_file(session, _store(), row)
    except FileStoreUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"deleted": file_id}
