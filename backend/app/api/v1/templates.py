"""Template authoring endpoints (Stage 3) — draft, publish (gated), list, inspect.

The lifecycle (ADR-0002 #14/#15): POST /templates saves a *draft* (new name -> v1; a name
with only published versions -> a fresh draft at latest+1; an existing draft is replaced in
place). POST /templates/{name}/publish runs the publish gate and, only if it passes, flips
the draft to published — from then on that version is immutable; editing forks the next draft.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Template
from app.db.session import get_session
from app.schemas.template import DraftRequest, PublishResult, TemplateDetail, TemplateSummary
from app.templates.contract import TemplateContract
from app.templates.taxonomy import category_for, driver_for
from app.templates.validation import validate_for_publish

router = APIRouter()


@router.get("", response_model=list[TemplateSummary])
async def list_templates(
    session: AsyncSession = Depends(get_session),
) -> list[TemplateSummary]:
    return [_summary(t) for t in await repo.list_templates(session)]


@router.get("/{name}", response_model=TemplateDetail)
async def get_template(
    name: str,
    version: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> TemplateDetail:
    template = (
        await repo.get_template_version(session, name, version)
        if version is not None
        else await repo.latest_template_any_status(session, name)
    )
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no template '{name}'")
    return TemplateDetail(
        **_summary(template).model_dump(),
        contract=template.contract,
        parameters=template.parameters,
    )


@router.post("", response_model=TemplateDetail)
async def save_draft(
    req: DraftRequest, session: AsyncSession = Depends(get_session)
) -> TemplateDetail:
    try:
        contract = TemplateContract.model_validate(req.contract)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"contract does not parse: {exc}"
        ) from exc

    draft = await repo.get_draft(session, contract.name)
    if draft is None:
        latest = await repo.latest_template_any_status(session, contract.name)
        draft = Template(
            name=contract.name,
            version=(latest.version + 1) if latest else 1,
            status="draft",
        )
        session.add(draft)
    # The server owns the version number; the body's is overwritten for consistency.
    contract.version = draft.version
    draft.agent_class = contract.agent_class
    draft.agent_surface = contract.agent_surface
    draft.risk = contract.risk
    draft.contract = contract.model_dump(mode="json")
    draft.parameters = {k: v.model_dump(mode="json") for k, v in contract.parameters.items()}
    await session.commit()
    await session.refresh(draft)
    return TemplateDetail(
        **_summary(draft).model_dump(), contract=draft.contract, parameters=draft.parameters
    )


@router.post("/{name}/publish", response_model=PublishResult)
async def publish_template(
    name: str, session: AsyncSession = Depends(get_session)
) -> PublishResult:
    draft = await repo.get_draft(session, name)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no draft for template '{name}'")

    contract = TemplateContract.model_validate(draft.contract)
    violations = validate_for_publish(contract)
    if violations:
        # The gate: an ungoverned template never becomes publishable.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "publish refused by the governance gate", "violations": violations},
        )

    draft.status = "published"
    await session.commit()
    return PublishResult(name=draft.name, version=draft.version, status=draft.status)


def _summary(t: Template) -> TemplateSummary:
    contract = TemplateContract.model_validate(t.contract)
    return TemplateSummary(
        name=t.name,
        version=t.version,
        status=t.status,
        agent_class=t.agent_class,
        category=category_for(contract.agent_class),
        driver=driver_for(contract.agent_class),
        agent_surface=t.agent_surface,
        risk=t.risk,
        step_count=len(t.contract.get("steps", [])),
        created_at=t.created_at,
    )
