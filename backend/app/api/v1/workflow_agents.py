"""Workflow agents and their cases (ADR-0030) — HTTP only.

Every route is tenant-scoped; another workspace's agent or case is a 404. Creating an agent, a
case, or computing one needs the workflow's module to run in the workspace (ADR-0028): a module
the workspace dropped leaves its records readable, not usable.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_tenant, workspace_modules
from app.db import repo
from app.db.models import WorkflowAgent, WorkflowCase
from app.db.session import get_session
from app.kernel.modules import Module, installed, workflow as find_workflow
from app.kernel.workflows import Workflow
from app.workflow_agents import service

agents_router = APIRouter()
cases_router = APIRouter()


class NewAgent(BaseModel):
    workflow: str
    name: str = Field(min_length=1, max_length=120)


class Settings(BaseModel):
    settings: dict


class NewCase(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class InputSave(BaseModel):
    revision: int
    input: dict


class Attach(BaseModel):
    file_id: str


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such {what}") from exc


def _workflow(key: str) -> tuple[Module, Workflow]:
    found = find_workflow(key)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no workflow '{key}'")
    return found


def _usable(module: Module, running: list[Module]) -> None:
    if module.key not in {m.key for m in running}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"this workflow comes with the {module.label} module, which this workspace does not run",
        )


async def _agent(session, agent_id: str, tenant) -> WorkflowAgent:
    agent = await repo.get_workflow_agent(session, _uuid(agent_id, "agent"), tenant.id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such agent")
    return agent


async def _case(session, case_id: str, tenant) -> WorkflowCase:
    case = await repo.get_workflow_case(session, _uuid(case_id, "case"), tenant.id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such case")
    return case


def _agent_summary(agent: WorkflowAgent) -> dict:
    found = find_workflow(agent.workflow_key)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "module": agent.module_key,
        "workflow": found[1].public() if found else {"key": agent.workflow_key},
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


def _case_summary(case: WorkflowCase) -> dict:
    return {
        "id": str(case.id),
        "title": case.title,
        "status": case.status,
        "input_revision": case.input_revision,
        "computed_revision": case.computed_revision,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


def _file(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
    }


async def _case_detail(session: AsyncSession, case: WorkflowCase, tenant) -> dict:
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    found = find_workflow(agent.workflow_key) if agent else None
    events = await repo.workflow_case_events(session, case.id)
    return {
        **_case_summary(case),
        "agent": _agent_summary(agent) if agent else None,
        "schemas": (
            {k: v for k, v in found[1].schemas().items() if k != "settings"} if found else None
        ),
        "input": case.input,
        "rules_version": case.rules_version,
        "result": case.result,
        "errors": case.errors,
        "questions": case.questions,
        "assumptions": case.assumptions,
        "problems": case.problems,
        "files": [_file(f) for f in await repo.workflow_case_files(session, case.id)],
        # The history without the results it recorded: those are read from the event itself.
        "events": [
            {
                "kind": e.kind,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                **{k: v for k, v in (e.payload or {}).items() if k in ("revision", "status", "name")},
            }
            for e in events
        ],
    }


# --- agents ---------------------------------------------------------------------------------


@agents_router.get("/workflows")
async def list_workflows() -> list[dict]:
    """What a workflow agent can be created from: every installed module's workflows. A workspace
    picks a kind of agent and then a workflow, never a domain first (ADR-0031)."""
    return [{**w.public(), "module": m.key} for m in installed().values() for w in m.workflows]


@agents_router.get("")
async def list_agents(
    session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> list[dict]:
    return [_agent_summary(a) for a in await repo.list_workflow_agents(session, tenant.id)]


@agents_router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    req: NewAgent,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    running: list[Module] = Depends(workspace_modules),
) -> dict:
    module, workflow = _workflow(req.workflow)
    # Choosing the workflow is choosing its module: the workspace runs it from now on.
    await repo.add_workspace_module(session, tenant.id, module.key)
    agent = service.new_agent(tenant.id, module.key, workflow, req.name.strip())
    session.add(agent)
    await session.commit()
    return _agent_summary(agent)


@agents_router.get("/{agent_id}")
async def get_agent(
    agent_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    agent = await _agent(session, agent_id, tenant)
    found = find_workflow(agent.workflow_key)
    return {
        **_agent_summary(agent),
        "settings": agent.settings,
        "settings_schema": found[1].schemas()["settings"] if found else None,
        "cases": [
            _case_summary(c) for c in await repo.list_workflow_cases(session, agent.id, tenant.id)
        ],
    }


@agents_router.put("/{agent_id}/settings")
async def save_settings(
    agent_id: str,
    req: Settings,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    agent = await _agent(session, agent_id, tenant)
    agent.settings = req.settings
    await session.commit()
    return {"settings": agent.settings}


@agents_router.post("/{agent_id}/cases", status_code=status.HTTP_201_CREATED)
async def create_case(
    agent_id: str,
    req: NewCase,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    running: list[Module] = Depends(workspace_modules),
) -> dict:
    agent = await _agent(session, agent_id, tenant)
    module, workflow = _workflow(agent.workflow_key)
    _usable(module, running)
    case = service.new_case(session, agent, workflow, req.title.strip())
    await service.created(session, case)
    await session.commit()
    return _case_summary(case)


# --- cases ----------------------------------------------------------------------------------


@cases_router.get("/{case_id}")
async def get_case(
    case_id: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    return await _case_detail(session, await _case(session, case_id, tenant), tenant)


@cases_router.put("/{case_id}/input")
async def save_input(
    case_id: str,
    req: InputSave,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    case = await _case(session, case_id, tenant)
    try:
        service.save_input(session, case, req.revision, req.input)
    except service.StaleInput as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant)


@cases_router.post("/{case_id}/compute")
async def compute_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    running: list[Module] = Depends(workspace_modules),
) -> dict:
    case = await _case(session, case_id, tenant)
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    module, workflow = _workflow(agent.workflow_key)
    _usable(module, running)
    service.compute(session, case, agent, workflow)
    await session.commit()
    return await _case_detail(session, case, tenant)


@cases_router.post("/{case_id}/files")
async def attach_file(
    case_id: str,
    req: Attach,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    case = await _case(session, case_id, tenant)
    file = await repo.get_stored_file(session, _uuid(req.file_id, "file"), tenant.id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    if file.id not in {f.id for f in await repo.workflow_case_files(session, case.id)}:
        service.attach(session, case, file)
        try:
            await session.commit()
        except IntegrityError:  # attached concurrently: the file is there either way
            await session.rollback()
    return await _case_detail(session, case, tenant)


@cases_router.delete("/{case_id}/files/{file_id}")
async def detach_file(
    case_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
) -> dict:
    case = await _case(session, case_id, tenant)
    file = await repo.get_stored_file(session, _uuid(file_id, "file"), tenant.id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    await service.detach(session, case, file)
    await session.commit()
    return await _case_detail(session, case, tenant)
