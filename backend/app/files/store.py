"""Where file bytes live, behind one small protocol (ADR-0029).

The database row (`stored_file`) is the truth about a file; a blob is only its bytes, addressed by
a key the platform makes up (`<tenant>/<file id>`), never by anything a user typed. Swapping where
blobs live is a new `BlobStore`, not a change to anything that uses files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

from app.config import settings


class FileStoreUnavailable(Exception):
    """The file store is not configured, or did not answer. Nothing was stored."""


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, media_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None:
        """Remove the blob; a blob that is already gone is not an error."""
        ...


class LocalBlobStore:
    """Files on this machine's disk. Development and tests only."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError(f"key escapes the store: {key!r}")
        return path

    async def put(self, key: str, data: bytes, media_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise FileStoreUnavailable(f"blob {key} is missing") from exc

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class SupabaseBlobStore:
    """A private Supabase Storage bucket, reached over its REST API with the service-role key.

    The bucket is created on first use if it does not exist, private: every read goes through
    this backend, which checks the workspace first.
    """

    def __init__(
        self,
        base_url: str,
        service_key: str,
        bucket: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
        self._transport = transport
        self._bucket_ready = False

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=30, transport=self._transport)

    async def _ensure_bucket(self, client: httpx.AsyncClient) -> None:
        if self._bucket_ready:
            return
        found = await client.get(f"{self._url}/bucket/{self._bucket}")
        if found.status_code != 200:
            created = await client.post(
                f"{self._url}/bucket",
                json={"id": self._bucket, "name": self._bucket, "public": False},
            )
            if created.status_code not in (200, 201):
                raise FileStoreUnavailable(
                    f"cannot create bucket {self._bucket}: {created.status_code} {created.text}"
                )
        self._bucket_ready = True

    async def put(self, key: str, data: bytes, media_type: str) -> None:
        try:
            async with self._client() as client:
                await self._ensure_bucket(client)
                res = await client.post(
                    f"{self._url}/object/{self._bucket}/{key}",
                    content=data,
                    headers={"Content-Type": media_type, "x-upsert": "false"},
                )
        except httpx.HTTPError as exc:
            raise FileStoreUnavailable(f"storage did not answer: {exc}") from exc
        if res.status_code not in (200, 201):
            raise FileStoreUnavailable(f"storage refused the upload: {res.status_code} {res.text}")

    async def get(self, key: str) -> bytes:
        try:
            async with self._client() as client:
                res = await client.get(f"{self._url}/object/{self._bucket}/{key}")
        except httpx.HTTPError as exc:
            raise FileStoreUnavailable(f"storage did not answer: {exc}") from exc
        if res.status_code != 200:
            raise FileStoreUnavailable(f"storage refused the download: {res.status_code}")
        return res.content

    async def delete(self, key: str) -> None:
        try:
            async with self._client() as client:
                res = await client.request(
                    "DELETE", f"{self._url}/object/{self._bucket}", json={"prefixes": [key]}
                )
        except httpx.HTTPError as exc:
            raise FileStoreUnavailable(f"storage did not answer: {exc}") from exc
        if res.status_code != 200:
            raise FileStoreUnavailable(f"storage refused the delete: {res.status_code}")


_store: BlobStore | None = None


def blob_store() -> BlobStore:
    """The configured store, built once. Fails closed: production never writes to local disk."""
    global _store
    if _store is not None:
        return _store
    if settings.files_backend == "supabase":
        if not (settings.supabase_url and settings.supabase_service_key):
            raise FileStoreUnavailable(
                "file storage needs NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
            )
        _store = SupabaseBlobStore(
            settings.supabase_url, settings.supabase_service_key, settings.files_bucket
        )
    elif settings.files_backend == "local":
        if settings.app_env == "production":
            raise FileStoreUnavailable(
                "file storage is not configured: set FILES_BACKEND=supabase in production"
            )
        _store = LocalBlobStore(settings.files_dir)
    else:
        raise FileStoreUnavailable(f"unknown FILES_BACKEND {settings.files_backend!r}")
    return _store
