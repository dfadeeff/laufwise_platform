"""Storing and removing a workspace's files (ADR-0029) — no FastAPI here.

Order matters. On upload the blob is written before the row, so a row never points at nothing;
if the row then loses a race to an identical upload, the blob is removed again and the winner is
returned. On delete the blob goes first, so a failed delete leaves a row the workspace can retry
rather than an invisible blob.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import StoredFile
from app.files.content import MAX_FILE_BYTES, safe_name, sniff
from app.files.store import BlobStore


class FileRejected(Exception):
    """The upload is not a file the platform keeps. Nothing was stored."""


async def store_file(
    session: AsyncSession, store: BlobStore, tenant_id: uuid.UUID, name: str, data: bytes
) -> tuple[StoredFile, bool]:
    """Keep `data` for this workspace. Returns the file and whether it is new: the same content
    uploaded again is the file already stored."""
    if not data:
        raise FileRejected("the file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected(f"the file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    name = safe_name(name)
    media_type = sniff(data, name)
    if media_type is None:
        raise FileRejected("only PDF, PNG, JPEG and UTF-8 text files can be stored")

    digest = hashlib.sha256(data).hexdigest()
    existing = await repo.stored_file_by_hash(session, tenant_id, digest)
    if existing is not None:
        return existing, False

    file_id = uuid.uuid4()
    key = f"{tenant_id}/{file_id}"
    await store.put(key, data, media_type)
    row = StoredFile(
        id=file_id,
        tenant_id=tenant_id,
        name=name,
        media_type=media_type,
        size_bytes=len(data),
        sha256=digest,
        storage_key=key,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await store.delete(key)
        winner = await repo.stored_file_by_hash(session, tenant_id, digest)
        if winner is None:
            raise
        return winner, False
    return row, True


async def remove_file(session: AsyncSession, store: BlobStore, row: StoredFile) -> None:
    await store.delete(row.storage_key)
    await session.delete(row)
    await session.commit()
