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

from app.api.deps import Principal, current_principal, current_tenant, workspace_modules
from app.approvals import service as approvals
from app.db import repo
from app.db.models import WorkflowAgent, WorkflowCase
from app.db.session import get_session
from app.extraction.provenance import found_in, unconfirmed
from app.extraction.reader import Document, ReaderUnavailable, document_reader
from app.extraction.strict import strict
from app.extraction.text import text_of
from app.files.store import FileStoreUnavailable, blob_store
from app.notifications.mail import transport_for
from app.kernel.modules import Module, offered, workflow as find_workflow
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


def approval_view(approval, principal: Principal | None, case=None) -> dict | None:
    if approval is None:
        return None
    me = principal.user_id if principal else None
    return {
        "id": str(approval.id),
        "status": approval.status,
        "letters": approval.payload.get("letters", []),
        "requested_by_me": approval.requested_by == me,
        "requested_at": approval.created_at.isoformat() if approval.created_at else None,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "decided_by_me": approval.decided_by == me if approval.decided_by else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "note": approval.note,
        "deliveries": approval.deliveries,
        # Whether it is about the case as it is now: after a correction, an earlier approval
        # still shows what was sent, but no longer describes these letters.
        "current": (
            case is not None
            and approvals.fingerprint(case, approval.payload) == approval.fingerprint
        ),
        # The server decides again when it is asked; this only spares a button that would fail.
        "can_decide": (
            approval.status == "pending"
            and principal is not None
            and principal.is_admin
            and approval.requested_by != me
        ),
    }


async def _case_detail(
    session: AsyncSession, case: WorkflowCase, tenant, principal: Principal | None = None
) -> dict:
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    history = await repo.case_approvals(session, case.id, tenant.id)
    found = find_workflow(agent.workflow_key) if agent else None
    events = await repo.workflow_case_events(session, case.id)
    return {
        **_case_summary(case),
        "agent": _agent_summary(agent) if agent else None,
        "schemas": (
            {k: v for k, v in found[1].schemas().items() if k != "settings"} if found else None
        ),
        "input": case.input,
        "input_source": case.input_source,
        "to_check": case.to_check,
        "rules_version": case.rules_version,
        "result": case.result,
        "drafts": case.drafts,
        "errors": case.errors,
        "questions": case.questions,
        "assumptions": case.assumptions,
        "problems": case.problems,
        "files": [_file(f) for f in await repo.workflow_case_files(session, case.id)],
        "approval": approval_view(history[0] if history else None, principal, case),
        # The history without the results it recorded: those are read from the event itself.
        "events": [
            {
                "kind": e.kind,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                **{k: v for k, v in (e.payload or {}).items() if k in ("revision", "status", "name", "model", "sent", "failed")},
            }
            for e in events
        ],
    }


# --- agents ---------------------------------------------------------------------------------


@agents_router.get("/workflows")
async def list_workflows(running: list[Module] = Depends(workspace_modules)) -> list[dict]:
    """What this workspace can create workflow agents from: its own modules' workflows, or every
    module's before it runs any."""
    return [{**w.public(), "module": m.key} for m in offered(running) for w in m.workflows]


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
    if module.key not in {m.key for m in running}:
        # The first agent from a module is how a workspace starts running it.
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
    case_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    return await _case_detail(session, await _case(session, case_id, tenant), tenant, principal)


@cases_router.put("/{case_id}/input")
async def save_input(
    case_id: str,
    req: InputSave,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    case = await _case(session, case_id, tenant)
    try:
        service.save_input(session, case, req.revision, req.input)
        approvals.withdraw_pending(
            session, case, await repo.case_approvals(session, case.id, tenant.id)
        )
    except service.StaleInput as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant, principal)


@cases_router.post("/{case_id}/compute")
async def compute_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
    running: list[Module] = Depends(workspace_modules),
) -> dict:
    case = await _case(session, case_id, tenant)
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    module, workflow = _workflow(agent.workflow_key)
    _usable(module, running)
    try:
        service.compute(session, case, agent, workflow)
    except service.UnconfirmedInput as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant, principal)


@cases_router.post("/{case_id}/extract")
async def extract_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
    running: list[Module] = Depends(workspace_modules),
) -> dict:
    """Read the attached documents into the input (ADR-0031). The model only fills the form; every
    amount and date it read is looked up in the documents' own text, and the input is not computed
    until a person has checked and saved it."""
    case = await _case(session, case_id, tenant)
    agent = await repo.get_workflow_agent(session, case.agent_id, tenant.id)
    module, workflow = _workflow(agent.workflow_key)
    _usable(module, running)
    files = await repo.workflow_case_files(session, case.id)
    if not files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "attach the documents to read first"
        )
    try:
        reader = document_reader()
        store = blob_store()
        documents = [
            Document(f.name, f.media_type, await store.get(f.storage_key)) for f in files
        ]
        form = workflow.schemas()["input"]
        data = await reader.read(strict(form), workflow.extraction_guide, documents)
    except (ReaderUnavailable, FileStoreUnavailable) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    to_check = unconfirmed(data, form, found_in([text_of(d) for d in documents]))
    service.apply_extraction(session, case, data, to_check, reader.model, files)
    approvals.withdraw_pending(session, case, await repo.case_approvals(session, case.id, tenant.id))
    await session.commit()
    return await _case_detail(session, case, tenant, principal)


@cases_router.post("/{case_id}/files")
async def attach_file(
    case_id: str,
    req: Attach,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
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
    return await _case_detail(session, case, tenant, principal)


@cases_router.delete("/{case_id}/files/{file_id}")
async def detach_file(
    case_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    case = await _case(session, case_id, tenant)
    file = await repo.get_stored_file(session, _uuid(file_id, "file"), tenant.id)
    if file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    await service.detach(session, case, file)
    await session.commit()
    return await _case_detail(session, case, tenant, principal)


class ApprovalAsk(BaseModel):
    # Recipient as the letter names them -> where the letter goes.
    emails: dict[str, str]


@cases_router.post("/{case_id}/approval", status_code=status.HTTP_201_CREATED)
async def request_approval(
    case_id: str,
    req: ApprovalAsk,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Put the case's letters up for a second person's approval (ADR-0033)."""
    case = await _case(session, case_id, tenant)
    if await transport_for(session, tenant.id) is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the platform cannot send mail yet; nothing to approve until it can",
        )
    try:
        approvals.request(
            session,
            case,
            await repo.case_approvals(session, case.id, tenant.id),
            principal.user_id,
            req.emails,
        )
    except approvals.Refused as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await session.commit()
    return await _case_detail(session, case, tenant, principal)
