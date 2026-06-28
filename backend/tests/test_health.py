"""The scaffold boots and serves health — the one behavior that must work today."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok() -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_run_not_wired_returns_501() -> None:
    # Honest behavior: the engine isn't wired yet, so a run reports 501 rather than
    # faking a result. This test should change to assert a real run once Phase 0 lands.
    res = client.post("/api/v1/runs", json={"runbook": "demo", "case": {}})
    assert res.status_code == 501