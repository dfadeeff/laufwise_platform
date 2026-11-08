"""A workspace's own workflow definitions (ADR-0039 step 5): HTTP only.

    GET    /definitions                       the shipped templates, and this workspace's own
    POST   /definitions                       copy a template into a private draft
    GET    /definitions/{key}                 published version, draft, guardrails, history
    PUT    /definitions/{key}/draft           save the draft (any member)
    POST   /definitions/{key}/draft/test      run the draft's cases, publish nothing
    POST   /definitions/{key}/draft/confirm   a person confirms a figure the builder proposed
    POST   /definitions/{key}/draft/confirm-check  a person accepts the builder's change to the check
    DELETE /definitions/{key}/draft           discard the draft
    POST   /definitions/{key}/publish         publish through the gate (administrators only)
    POST   /definitions/{key}/builder         the builder revises the draft (step 6); never publishes
    GET    /definitions/{key}/builder         its runs, newest first

A shipped template is read by every workspace and changed by none: it is copied first. Another
workspace's definition is a 404, never a 403, so its key cannot be probed. A draft's content is
untrusted input: it is bounded here, its code only ever runs in the sandbox, and it is published
only through the gate. While the builder works on a draft, nobody changes it by hand: its run
saves the draft when it ends.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, current_principal, current_tenant
from app.db import repo
from app.builder import service as builder
from app.db.models import BuilderRun, Definition
from app.db.session import get_session, get_sessionmaker
from app.definitions import service
from app.definitions.gate import GateRefused, guardrails
from app.kernel.definitions import DefinitionError, bundles_dir, workflow_from

router = APIRouter(dependencies=[Depends(current_tenant)])

# A definition is its forms, rules, guide, code and cases: tens of kilobytes; bounded well above.
_MAX_CONTENT_BYTES = 2_000_000


class NewDefinition(BaseModel):
    template: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=120)


class BuilderAsk(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)
    file_ids: list[str] = Field(default_factory=list, max_length=20)


def _run(run: BuilderRun) -> dict:
    return {
        "id": str(run.id),
        "state": run.state,
        "instruction": run.instruction,
        "file_ids": run.file_ids,
        "requested_by": run.requested_by,
        "model": run.model,
        "rounds": run.rounds,
        "summary": run.summary,
        "guardrail_changes": run.guardrail_changes,
        "changes": run.changes,
        "draft_version": run.draft_version,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


class Confirm(BaseModel):
    case: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=300)


class Draft(BaseModel):
    content: dict

    @field_validator("content")
    @classmethod
    def _size(cls, value: dict) -> dict:
        if len(json.dumps(value, default=str)) > _MAX_CONTENT_BYTES:
            raise ValueError(f"larger than {_MAX_CONTENT_BYTES // 1_000_000} MB")
        return value


def _summary(definition: Definition, published, draft) -> dict:
    content = (published or draft).content if (published or draft) else {}
    return {
        "key": definition.key,
        "module": definition.module_key,
        "label": content.get("label", definition.key),
        "summary": content.get("summary", ""),
        "owner": "platform" if definition.tenant_id is None else "workspace",
        "template": definition.template_key,
        "published_version": published.version if published else None,
        "draft_version": draft.version if draft else None,
    }


async def _readable(session: AsyncSession, key: str, tenant) -> Definition:
    definition = await repo.get_definition(session, key)
    if definition is None or definition.tenant_id not in (None, tenant.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such definition")
    return definition


async def _own(session: AsyncSession, key: str, tenant) -> Definition:
    definition = await _readable(session, key, tenant)
    if definition.tenant_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "a shipped template is not changed: copy it into this workspace first"
        )
    return definition


async def _not_building(session: AsyncSession, key: str, tenant) -> Definition:
    """The workspace's own definition, and no builder run on it: a run saves the draft when it
    ends, so a change made meanwhile would be lost without anyone being told."""
    definition = await _own(session, key, tenant)
    if await builder.working(session, definition):
        raise HTTPException(status.HTTP_409_CONFLICT, "the builder is working on this draft: wait until its run ends")
    return definition


@router.get("")
async def list_definitions(session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)) -> dict:
    async def rows(owner):
        return [
            _summary(d, await repo.latest_published_of(session, d.id), await repo.definition_draft(session, d.id))
            for d in await repo.definitions_of(session, owner)
        ]

    return {"templates": [r for r in await rows(None) if r["published_version"]], "own": await rows(tenant.id)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_definition(
    req: NewDefinition, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    try:
        draft = await service.create_from_template(session, tenant.id, req.template, req.label.strip())
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"key": draft.content["key"], "draft_version": draft.version}


@router.get("/{key}")
async def get_definition(key: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)) -> dict:
    definition = await _readable(session, key, tenant)
    published = await repo.latest_published_of(session, definition.id)
    draft = await repo.definition_draft(session, definition.id)
    panel = []
    if published is not None:
        panel = guardrails(workflow_from(published.content, bundles_dir(), version_id=str(published.id), gate=published.gate))
    return {
        **_summary(definition, published, draft),
        "published": (
            {"version": published.version, "content": published.content, "gate": published.gate,
             "published_at": published.published_at.isoformat() if published.published_at else None}
            if published else None
        ),
        "draft": {"version": draft.version, "content": draft.content} if draft else None,
        "guardrails": panel,
        "versions": [
            {"version": v.version, "status": v.status, "passed": (v.gate or {}).get("passed"),
             "published_at": v.published_at.isoformat() if v.published_at else None}
            for v in await repo.definition_versions(session, definition.id)
        ],
    }


@router.put("/{key}/draft")
async def save_draft(
    key: str, req: Draft, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)
) -> dict:
    definition = await _not_building(session, key, tenant)
    try:
        draft = await service.save_draft(session, key, definition.module_key, req.content, tenant.id)
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"key": key, "draft_version": draft.version}


@router.post("/{key}/draft/test")
async def test_draft(key: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)) -> dict:
    await _own(session, key, tenant)
    try:
        return await service.test_draft(session, key)
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/{key}/builder", status_code=status.HTTP_202_ACCEPTED)
async def ask_builder(
    key: str,
    req: BuilderAsk,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """The builder revises the draft from an instruction and documents (ADR-0039 D6). It runs
    after this answer; poll its runs. It writes the draft only: publishing stays a person's act."""
    definition = await _own(session, key, tenant)
    try:
        run = await builder.start(session, definition, req.instruction.strip(), req.file_ids, principal.user_id)
    except builder.Busy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    background.add_task(builder.execute, get_sessionmaker(), run.id)
    return _run(run)


@router.get("/{key}/builder")
async def builder_runs(key: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)) -> list[dict]:
    definition = await _own(session, key, tenant)
    return [_run(r) for r in await repo.builder_runs(session, definition.id)]


@router.post("/{key}/draft/confirm")
async def confirm_figure(
    key: str,
    req: Confirm,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """A person confirms one expected figure against the source its case names (ADR-0038 D4)."""
    await _not_building(session, key, tenant)
    try:
        await service.confirm_figure(session, key, req.case, req.path, principal.user_id)
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"key": key, "confirmed": f"{req.case}: {req.path}"}


@router.post("/{key}/draft/confirm-check")
async def confirm_check(
    key: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
) -> dict:
    """A person has read the builder's change to the check's code and accepts it (ADR-0039 step 6f)."""
    await _not_building(session, key, tenant)
    try:
        await service.confirm_check(session, key, principal.user_id)
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"key": key, "check_confirmed_by": principal.user_id}


@router.delete("/{key}/draft", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft(key: str, session: AsyncSession = Depends(get_session), tenant=Depends(current_tenant)) -> Response:
    await _not_building(session, key, tenant)
    await service.discard_draft(session, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{key}/publish")
async def publish(
    key: str,
    session: AsyncSession = Depends(get_session),
    tenant=Depends(current_tenant),
    principal: Principal = Depends(current_principal),
):
    await _not_building(session, key, tenant)
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an administrator of this workspace publishes")
    try:
        version = await service.publish(session, key)
    except GateRefused as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "report": exc.report})
    except DefinitionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"key": key, "version": version.version, "gate": version.gate}
