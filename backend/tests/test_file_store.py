"""The workspace file store (ADR-0029).

What is stored is decided by the bytes, not the name or the browser's claim; a workspace sees and
deletes only its own files; the same content is stored once; production never writes to local
disk; the Supabase store speaks Storage's REST API to a private bucket.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Awaitable, Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.config import settings
from app.db import repo
from app.db.models import StoredFile, Tenant
from app.files import store as store_module
from app.files.content import MAX_FILE_BYTES, safe_name, sniff
from app.files.store import (
    FileStoreUnavailable,
    LocalBlobStore,
    SupabaseBlobStore,
    blob_store,
)
from app.main import app

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
ZIP = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 16


# --- what a file is ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "name", "media_type"),
    [
        (PDF, "Hausgeld.pdf", "application/pdf"),
        (PDF, "no-extension", "application/pdf"),
        (PNG, "scan.png", "image/png"),
        (JPEG, "scan.jpg", "image/jpeg"),
        ("Grundsteuer 186,40 €\n".encode(), "notiz.txt", "text/plain"),
        (b"position;betrag\nMuell;198.00\n", "export.csv", "text/csv"),
        (ZIP, "Hausgeld.pdf", None),  # a name is not a type
        (b"\xff\xfe\x00\x00binary", "a.txt", None),
        (b"caf\xe9", "latin1.txt", None),  # not UTF-8
        (b"", "empty.pdf", None),
    ],
)
def test_the_type_comes_from_the_bytes(data, name, media_type) -> None:
    assert sniff(data, name) == media_type


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("Hausgeld 2025.pdf", "Hausgeld 2025.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\Abrechnung.pdf", "Abrechnung.pdf"),
        ("evil\x00\r\nname.pdf", "evilname.pdf"),
        ("...", "file"),
        ("a" * 300 + ".pdf", "a" * 200),
    ],
)
def test_a_display_name_is_only_a_name(given, kept) -> None:
    assert safe_name(given) == kept


# --- where bytes live ----------------------------------------------------------------------


def test_the_local_store_round_trips_and_stays_inside_its_root(tmp_path) -> None:
    store = LocalBlobStore(tmp_path)

    async def go():
        await store.put("t/1", PDF, "application/pdf")
        assert await store.get("t/1") == PDF
        await store.delete("t/1")
        await store.delete("t/1")  # already gone is not an error
        with pytest.raises(FileStoreUnavailable):
            await store.get("t/1")
        with pytest.raises(ValueError):
            await store.put("../outside", PDF, "application/pdf")

    asyncio.run(go())


def _supabase(handler) -> tuple[SupabaseBlobStore, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    store = SupabaseBlobStore(
        "https://proj.supabase.co", "service-key", "workspace-files",
        transport=httpx.MockTransport(record),
    )
    return store, seen


def test_the_supabase_store_creates_a_private_bucket_once_then_uploads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/bucket/workspace-files"):
            return httpx.Response(404, json={"error": "Bucket not found"})
        return httpx.Response(200, json={})

    store, seen = _supabase(handler)
    asyncio.run(store.put("t/1", PDF, "application/pdf"))
    asyncio.run(store.put("t/2", PDF, "application/pdf"))

    created = [r for r in seen if r.method == "POST" and r.url.path == "/storage/v1/bucket"]
    assert len(created) == 1
    assert json.loads(created[0].content) == {
        "id": "workspace-files", "name": "workspace-files", "public": False,
    }
    upload = [r for r in seen if r.url.path == "/storage/v1/object/workspace-files/t/1"][0]
    assert upload.headers["apikey"] == "service-key"
    assert upload.headers["authorization"] == "Bearer service-key"
    assert upload.headers["content-type"] == "application/pdf"
    assert upload.headers["x-upsert"] == "false"
    assert upload.content == PDF


def test_the_supabase_store_reads_deletes_and_reports_refusals() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/object/" in request.url.path:
            return httpx.Response(200, content=PDF)
        if request.method == "DELETE":
            assert json.loads(request.content) == {"prefixes": ["t/1"]}
            return httpx.Response(200, json=[])
        if request.method == "POST" and "/object/" in request.url.path:
            return httpx.Response(400, json={"error": "Duplicate"})
        return httpx.Response(200, json={})

    store, _ = _supabase(handler)
    assert asyncio.run(store.get("t/1")) == PDF
    asyncio.run(store.delete("t/1"))
    with pytest.raises(FileStoreUnavailable, match="refused the upload"):
        asyncio.run(store.put("t/1", PDF, "application/pdf"))


@pytest.fixture
def fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "_store", None)
    yield
    store_module._store = None


def test_production_never_stores_on_local_disk(monkeypatch, fresh_store) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "files_backend", "local")
    with pytest.raises(FileStoreUnavailable, match="FILES_BACKEND=supabase"):
        blob_store()


def test_supabase_storage_without_its_key_is_unavailable(monkeypatch, fresh_store) -> None:
    monkeypatch.setattr(settings, "files_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_service_key", None)
    with pytest.raises(FileStoreUnavailable, match="SUPABASE_SERVICE_ROLE_KEY"):
        blob_store()


# --- the API, against the database ---------------------------------------------------------


def _run_db(fn: Callable[[AsyncSession], Awaitable]):
    async def go():
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _db_reachable() -> bool:
    try:
        _run_db(lambda s: s.execute(select(1)))
        return True
    except Exception:
        return False


db = pytest.mark.skipif(not _db_reachable(), reason="Supabase DB not reachable")
client = TestClient(app)
_ORGS = ("org_test_files_a", "org_test_files_b")


def _as(org: str):
    async def dep() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                tenant = await repo.tenant_for_org(s, org, name=org)
                await s.commit()
                return tenant
        finally:
            await engine.dispose()

    return dep


def _cleanup() -> None:
    async def go(s: AsyncSession):
        for org in _ORGS:
            tenant = (
                await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))
            ).scalar_one_or_none()
            if tenant is not None:
                await s.execute(delete(StoredFile).where(StoredFile.tenant_id == tenant.id))
                await s.delete(tenant)
        await s.commit()

    _run_db(go)


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    local = LocalBlobStore(tmp_path)
    monkeypatch.setattr(store_module, "_store", local)
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_ORGS[0])
    yield local
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _upload(data: bytes, name: str, content_type: str = "application/octet-stream"):
    return client.post(
        "/api/v1/files", params={"name": name}, content=data,
        headers={"Content-Type": content_type},
    )


@db
def test_an_upload_is_stored_once_and_typed_by_its_bytes(workspaces) -> None:
    first = _upload(PDF, "Hausgeld 2025.pdf", content_type="text/html")
    again = _upload(PDF, "copy.pdf")

    assert first.status_code == 201
    assert first.json()["media_type"] == "application/pdf"  # the browser's claim is ignored
    assert first.json()["size_bytes"] == len(PDF)
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert [f["name"] for f in client.get("/api/v1/files").json()] == ["Hausgeld 2025.pdf"]


@db
def test_what_is_not_a_document_is_refused_before_it_is_stored(workspaces) -> None:
    assert _upload(ZIP, "Hausgeld.pdf").status_code == 415
    assert _upload(b"", "empty.pdf").status_code == 415
    too_large = _upload(b"%PDF-" + b"0" * MAX_FILE_BYTES, "huge.pdf")
    assert too_large.status_code == 413
    assert client.get("/api/v1/files").json() == []


@db
def test_a_download_is_the_file_as_an_attachment(workspaces) -> None:
    file_id = _upload(PDF, "Grundsteuer 2025.pdf").json()["id"]

    res = client.get(f"/api/v1/files/{file_id}/content")

    assert res.content == PDF
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].startswith("attachment;")
    assert "Grundsteuer%202025.pdf" in res.headers["content-disposition"]
    assert res.headers["x-content-type-options"] == "nosniff"


@db
def test_another_workspace_cannot_see_read_or_delete_a_file(workspaces) -> None:
    file_id = _upload(PDF, "Hausgeld.pdf").json()["id"]

    app.dependency_overrides[current_tenant] = _as(_ORGS[1])
    assert client.get("/api/v1/files").json() == []
    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    assert client.get(f"/api/v1/files/{file_id}/content").status_code == 404
    assert client.delete(f"/api/v1/files/{file_id}").status_code == 404
    # The same bytes are a separate file in the other workspace.
    assert _upload(PDF, "mine.pdf").json()["id"] != file_id


@db
def test_deleting_a_file_removes_its_bytes(workspaces) -> None:
    body = _upload(PNG, "scan.png").json()

    async def key_of(s: AsyncSession) -> str:
        stmt = select(StoredFile.storage_key).where(StoredFile.id == uuid.UUID(body["id"]))
        return (await s.execute(stmt)).scalar_one()

    key = _run_db(key_of)
    assert (workspaces._root / key).exists()

    assert client.delete(f"/api/v1/files/{body['id']}").status_code == 200
    assert client.delete(f"/api/v1/files/{body['id']}").status_code == 404
    assert not (workspaces._root / key).exists()


@db
def test_an_unconfigured_store_in_production_refuses_uploads(workspaces, monkeypatch) -> None:
    monkeypatch.setattr(store_module, "_store", None)
    monkeypatch.setattr(settings, "app_env", "production")
    # The tenant override stands in for a verified token; only the store is under test here.
    assert _upload(PDF, "a.pdf").status_code == 503
