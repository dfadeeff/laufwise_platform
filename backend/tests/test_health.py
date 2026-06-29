"""The scaffold boots and serves health — the one behavior that must work today."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# Run behavior is covered DB-free in test_runtime_localengine.py and against Postgres in
# test_persistence.py (the run endpoint now resolves templates from the DB).