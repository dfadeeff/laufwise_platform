"""Workflow agents and their cases (ADR-0030).

The forms come from the module's own dataclasses; a person corrects the input, never the result;
the kernel runs the workflow's check on every result and never shows one it refuses; questions
block figures; every save and computation is in the case's history; a file a case reads cannot be
deleted; another workspace sees nothing.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import current_tenant
from app.api.v1 import workflow_agents as routes
from app.config import settings
from app.db import repo
from app.db.models import StoredFile, Tenant, WorkflowAgent, WorkflowCase
from app.files import store as store_module
from app.files.store import LocalBlobStore
from app.kernel.modules import Module, ModuleError, validated, workflow
from app.kernel.schema import empty, object_schema, plain
from app.kernel.workflows import Computed, Workflow
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "betriebskosten"
_, BETRIEBSKOSTEN = workflow("betriebskosten")


# --- forms from dataclasses ----------------------------------------------------------------


def test_the_input_form_is_the_modules_own_case() -> None:
    schema = BETRIEBSKOSTEN.schemas()["input"]["properties"]

    assert list(schema) == [
        "unit", "period_start", "period_end", "positions", "tenancies", "lease", "heating",
    ]
    assert schema["period_start"] == {"type": "string", "format": "date",
                                      "title": "Abrechnungszeitraum von"}
    position = schema["positions"]["items"]["properties"]
    assert position["amount"]["format"] == "decimal"  # money never passes through a float
    assert {"value": "kabel_sammelvertrag", "label": "Kabelanschluss (Sammelvertrag)"} in (
        position["category"]["enum"]
    )
    assert position["category"]["nullable"] is True
    assert schema["heating"]["nullable"] is True
    assert schema["heating"]["properties"]["readings"]["type"] == "array"


def test_the_result_form_leaves_out_what_the_kernel_shows_itself() -> None:
    result = BETRIEBSKOSTEN.schemas()["result"]["properties"]
    assert "questions" not in result and "assumptions" not in result
    assert result["statements"]["items"]["properties"]["lines"]["type"] == "array"


def test_a_new_form_starts_blank() -> None:
    blank = empty(BETRIEBSKOSTEN.schemas()["input"])
    assert blank["positions"] == [] and blank["heating"] is None and blank["unit"] == ""


def test_a_value_becomes_json_without_floats() -> None:
    @dataclass
    class Money:
        amount: Decimal
        on: date
        parts: tuple[Decimal, ...]

    assert plain(Money(Decimal("1.10"), date(2025, 1, 2), (Decimal("0.10"),))) == {
        "amount": "1.10", "on": "2025-01-02", "parts": ["0.10"],
    }


def test_a_type_with_no_form_is_refused() -> None:
    @dataclass
    class Floaty:
        rate: float

    with pytest.raises(TypeError, match="has no form"):
        object_schema(Floaty)


def _workflow(key: str, input_type: type) -> Workflow:
    return Workflow(
        key=key, label=key, summary="", input_type=input_type, settings_type=input_type,
        result_type=input_type, parse=lambda i, s: i, compute=lambda p: Computed(p, {}),
        check=lambda p, v: [], rules_version="1",
    )


def _module(key: str, *workflows: Workflow) -> Module:
    return Module(key=key, label=key, summary="", pitch=(), intro="", workflows=workflows)


def test_a_module_whose_workflow_has_no_form_stops_the_boot() -> None:
    @dataclass
    class Floaty:
        rate: float

    with pytest.raises(ModuleError, match="has no form"):
        validated([_module("a", _workflow("w", Floaty))], ("a",))


def test_two_modules_cannot_offer_one_workflow() -> None:
    @dataclass
    class Plain:
        name: str

    with pytest.raises(ModuleError, match="offered by a and b"):
        validated(
            [_module("a", _workflow("w", Plain)), _module("b", _workflow("w", Plain))],
            ("a", "b"),
        )


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
_PROPERTY, _PRACTICE = "org_test_wf_property", "org_test_wf_practice"


def _as(org: str, module: str):
    async def dep() -> Tenant:
        engine = create_async_engine(settings.sqlalchemy_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                tenant = await repo.tenant_for_org(s, org, name=org)
                await repo.add_workspace_module(s, tenant.id, module)
                await s.commit()
                return tenant
        finally:
            await engine.dispose()

    return dep


def _cleanup() -> None:
    async def go(s: AsyncSession):
        for org in (_PROPERTY, _PRACTICE):
            tenant = (
                await s.execute(select(Tenant).where(Tenant.clerk_org_id == org))
            ).scalar_one_or_none()
            if tenant is None:
                continue
            await s.execute(delete(WorkflowCase).where(WorkflowCase.tenant_id == tenant.id))
            await s.execute(delete(WorkflowAgent).where(WorkflowAgent.tenant_id == tenant.id))
            await s.execute(delete(StoredFile).where(StoredFile.tenant_id == tenant.id))
            await s.delete(tenant)
        await s.commit()

    _run_db(go)


@pytest.fixture
def property_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_store", LocalBlobStore(tmp_path))
    _cleanup()
    app.dependency_overrides[current_tenant] = _as(_PROPERTY, "immobilien")
    yield
    app.dependency_overrides.pop(current_tenant, None)
    _cleanup()


def _fixture(name: str) -> dict:
    raw = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    return json.loads(json.dumps(raw, default=str))  # dates as the editor sends them


def _agent(name: str = "Abrechnungen Lindenstraße") -> dict:
    res = client.post("/api/v1/workflow-agents", json={"workflow": "betriebskosten", "name": name})
    assert res.status_code == 201, res.text
    return res.json()


def _case(agent_id: str, title: str = "Wohnung 3 · 2025") -> dict:
    res = client.post(f"/api/v1/workflow-agents/{agent_id}/cases", json={"title": title})
    assert res.status_code == 201, res.text
    return client.get(f"/api/v1/cases/{res.json()['id']}").json()


def _save(case: dict, data: dict) -> dict:
    res = client.put(
        f"/api/v1/cases/{case['id']}/input", json={"revision": case["input_revision"], "input": data}
    )
    assert res.status_code == 200, res.text
    return res.json()


def _compute(case: dict) -> dict:
    res = client.post(f"/api/v1/cases/{case['id']}/compute")
    assert res.status_code == 200, res.text
    return res.json()


@db
def test_a_property_workspace_offers_the_settlement_and_a_practice_does_not(
    property_workspace,
) -> None:
    assert [w["key"] for w in client.get("/api/v1/workflow-agents/workflows").json()] == [
        "betriebskosten"
    ]
    app.dependency_overrides[current_tenant] = _as(_PRACTICE, "praxis")
    assert client.get("/api/v1/workflow-agents/workflows").json() == []
    refused = client.post(
        "/api/v1/workflow-agents", json={"workflow": "betriebskosten", "name": "x"}
    )
    assert refused.status_code == 403


@db
def test_a_case_goes_from_blank_input_to_a_checked_result(property_workspace) -> None:
    agent = _agent()
    case = _case(agent["id"])
    assert case["status"] == "draft" and case["input"]["positions"] == []

    blank = _compute(case)
    assert blank["status"] == "draft" and blank["errors"] and blank["result"] is None

    saved = _save(blank, _fixture("nk01"))
    assert saved["input_revision"] == 2

    # The lease and the association split by different keys: asked, not assumed.
    asked = _compute(saved)
    assert asked["status"] == "blocked" and asked["result"] is None
    assert "Gesamtabrechnung der WEG" in asked["questions"][0]

    # Answered once, as the agent's standing decision.
    client.put(
        f"/api/v1/workflow-agents/{agent['id']}/settings",
        json={"settings": {"basis": "unit_share"}},
    )
    ready = _compute(asked)
    assert ready["status"] == "ready"
    assert ready["rules_version"] == "catalogue v1"
    assert ready["result"]["allocable_total"] == "2331.40"
    assert ready["result"]["statements"][0]["balance"] == "171.40"
    assert ready["assumptions"]
    assert [e["kind"] for e in ready["events"]] == [
        "created", "computed", "input_saved", "computed", "computed",
    ]


@db
def test_saving_the_input_clears_the_result_and_a_stale_save_is_refused(
    property_workspace,
) -> None:
    agent = _agent()
    client.put(
        f"/api/v1/workflow-agents/{agent['id']}/settings",
        json={"settings": {"basis": "unit_share"}},
    )
    case = _compute(_save(_case(agent["id"]), _fixture("nk02")))
    assert case["status"] == "ready" and len(case["result"]["statements"]) == 3

    stale = client.put(
        f"/api/v1/cases/{case['id']}/input", json={"revision": 1, "input": _fixture("nk02")}
    )
    assert stale.status_code == 409

    edited = _save(case, {**_fixture("nk02"), "unit": "Wohnung 5, korrigiert"})
    assert edited["status"] == "draft" and edited["result"] is None


@db
def test_a_result_the_check_refuses_is_never_shown(property_workspace, monkeypatch) -> None:
    agent = _agent()
    client.put(
        f"/api/v1/workflow-agents/{agent['id']}/settings",
        json={"settings": {"basis": "unit_share"}},
    )
    case = _save(_case(agent["id"]), _fixture("nk01"))
    module, real = workflow("betriebskosten")
    broken = dataclasses.replace(real, check=lambda parsed, value: ["shares do not add up"])
    monkeypatch.setattr(routes, "find_workflow", lambda key: (module, broken))

    failed = _compute(case)

    assert failed["status"] == "failed"
    assert failed["result"] is None
    assert failed["problems"] == ["shares do not add up"]


@db
def test_a_file_a_case_reads_cannot_be_deleted_until_detached(property_workspace) -> None:
    case = _case(_agent()["id"])
    pdf = (Path(__file__).parents[2] / "docs/use_cases/real_estate/bo/NK-Test-01 Auftrag.pdf")
    data = pdf.read_bytes() if pdf.exists() else b"%PDF-1.7\n%%EOF\n"
    file_id = client.post(
        "/api/v1/files", params={"name": "Hausgeld.pdf"}, content=data
    ).json()["id"]

    attached = client.post(f"/api/v1/cases/{case['id']}/files", json={"file_id": file_id}).json()
    assert [f["id"] for f in attached["files"]] == [file_id]
    assert client.delete(f"/api/v1/files/{file_id}").status_code == 409

    client.delete(f"/api/v1/cases/{case['id']}/files/{file_id}")
    assert client.delete(f"/api/v1/files/{file_id}").status_code == 200


@db
def test_another_workspace_sees_no_agent_and_no_case(property_workspace) -> None:
    agent = _agent()
    case = _case(agent["id"])

    app.dependency_overrides[current_tenant] = _as(_PRACTICE, "immobilien")
    assert client.get("/api/v1/workflow-agents").json() == []
    assert client.get(f"/api/v1/workflow-agents/{agent['id']}").status_code == 404
    assert client.get(f"/api/v1/cases/{case['id']}").status_code == 404
    assert client.post(f"/api/v1/cases/{case['id']}/compute").status_code == 404
