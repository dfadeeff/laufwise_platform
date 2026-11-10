"""Starting a replica must not interrupt a live builder or let an expired one save a draft."""

import asyncio
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.builder import service as builder
from app.db import repo


def test_startup_only_interrupts_expired_builder_leases(monkeypatch):
    now = datetime.now(timezone.utc)
    live = SimpleNamespace(state="running", lease_expires_at=now + timedelta(minutes=1))
    expired = SimpleNamespace(state="running", lease_expires_at=now - timedelta(seconds=1))
    committed = []
    class Session:
        async def commit(self):
            committed.append(True)
    @asynccontextmanager
    async def factory():
        yield Session()
    async def active(*a):
        return [live, expired]
    monkeypatch.setattr(repo, "active_builder_runs", active)
    assert asyncio.run(builder.interrupt_stale(factory)) == 1
    assert live.state == "running"
    assert expired.state == "interrupted"


def test_an_execution_that_lost_its_lease_cannot_save_a_draft(monkeypatch):
    # Exercise the final save seam, independently of the model and sandbox.
    called = []
    async def lost(*args, **kwargs):
        return None
    async def save(*args, **kwargs):
        called.append("saved")
    monkeypatch.setattr(repo, "owned_builder_run", lost, raising=False)
    monkeypatch.setattr(builder.definitions, "save_draft", save)
    import pytest
    with pytest.raises(builder.LeaseLost):
        asyncio.run(builder.save_owned_draft(
            SimpleNamespace(no_autoflush=nullcontext()), uuid.uuid4(), "old-worker",
            SimpleNamespace(key="x", module_key="demo", tenant_id=uuid.uuid4()), {},
        ))
    assert called == []
