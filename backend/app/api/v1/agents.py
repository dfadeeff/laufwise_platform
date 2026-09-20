"""HTTP adapter for the customer agent workspace."""

import asyncio
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.api.deps import current_tenant
from app.agents import service
from app.agents.config import AgentConfig
from app.db import agents as store, repo
from app.workloads.conversational.skills import load_skills
from app.db.session import get_session

router = APIRouter()


class NewAgent(BaseModel):
    """What the customer types before the agent exists: a name, their practice, a language."""

    name: str = Field(default="Receptionist", min_length=1, max_length=120)
    practice_name: str = Field(default="", max_length=160)
    locale: Literal["de", "en", "ru", "ar"] = "de"


class SaveDraft(BaseModel):
    generation: int
    config: AgentConfig


class Generation(BaseModel):
    generation: int


class Activation(BaseModel):
    instance_id: str
    connection_id: str
    phone_number: str


class ConnectionCheck(BaseModel):
    connection_id: str


@router.get("")
async def list_agents(tenant=Depends(current_tenant), session: AsyncSession = Depends(get_session)):
    return [
        await service.detail(session, agent)
        for agent in await store.list_agents(session, tenant.id)
    ]


@router.post("")
async def create_agent(
    req: NewAgent | None = None,
    tenant=Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
):
    seed = req or NewAgent()
    config = AgentConfig(
        name=seed.name, practice_name=seed.practice_name, locale=seed.locale
    )
    agent = await store.create_agent(session, tenant.id, config.model_dump())
    await session.commit()
    return await service.detail(session, agent)


@router.get("/capabilities")
async def list_capabilities():
    """The capability catalogue the Studio renders its toggles from.

    Declared before `/{agent_id}` so the path does not resolve to an agent called "capabilities".
    Served from the same loader the runtime uses, so a skill added to the repo appears in the
    Studio without a second list to keep in sync — and a practice never sees a toggle for a
    capability the model does not have.
    """
    return [
        {
            "name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "tools": list(skill.tools),
            "state_changing": skill.is_state_changing,
            # The connection roles this capability cannot work without, straight from its
            # manifest — so the Studio can say "needs a calendar" without knowing what a
            # calendar is.
            "requires": list(skill.requires),
        }
        for skill in load_skills()
    ]


@router.delete("/{agent_id}/callers")
async def forget_callers(
    agent_id: str, tenant=Depends(current_tenant), session: AsyncSession = Depends(get_session)
):
    """Forget everyone this agent remembers.

    Article 17 is a right somebody will eventually exercise, and a right whose only implementation
    is an engineer running SQL is not one a practice can honour. The agent keeps answering calls;
    it simply stops recognising anyone until they identify themselves again.
    """
    agent = await service.get_agent(session, tenant.id, agent_id)
    forgotten = await repo.forget_callers(session, tenant_id=tenant.id, agent_id=agent.id)
    return {"forgotten": forgotten}


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, tenant=Depends(current_tenant), session: AsyncSession = Depends(get_session)
):
    return await service.detail(session, await service.get_agent(session, tenant.id, agent_id))


@router.post("/{agent_id}/draft")
async def save_draft(
    agent_id: str,
    req: SaveDraft,
    tenant=Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
):
    agent = await service.get_agent(session, tenant.id, agent_id, lock=True)
    await service.save(session, agent, req.config, req.generation)
    await session.commit()
    return await service.detail(session, agent)


@router.post("/{agent_id}/publish")
async def publish(
    agent_id: str,
    req: Generation,
    tenant=Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
):
    agent = await service.get_agent(session, tenant.id, agent_id, lock=True)
    service.check_generation(agent, req.generation)
    await service.make_snapshot(session, agent, kind="published")
    await session.commit()
    return await service.detail(session, agent)


@router.post("/{agent_id}/check")
async def check_connection(
    agent_id: str,
    req: ConnectionCheck,
    tenant=Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
):
    agent = await service.get_agent(session, tenant.id, agent_id)
    connection = await service.owned_connection(session, tenant.id, req.connection_id)
    try:
        return await asyncio.to_thread(
            service.check_calendar, connection, AgentConfig.model_validate(agent.draft)
        )
    except service.StudioError:
        raise
    except Exception as exc:
        raise HTTPException(
            422, "Calendar access could not be verified. Reconnect and check your room IDs."
        ) from exc


@router.post("/{agent_id}/activate")
async def activate(
    agent_id: str,
    req: Activation,
    tenant=Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
):
    agent = await service.get_agent(session, tenant.id, agent_id, lock=True)
    try:
        await service.activate(
            session, agent, req.instance_id, req.connection_id, req.phone_number.strip()
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            409, "This number was assigned by another session. Reload and try again."
        ) from exc
    return await service.detail(session, agent)


@router.post("/{agent_id}/pause")
async def pause(
    agent_id: str, tenant=Depends(current_tenant), session: AsyncSession = Depends(get_session)
):
    agent = await service.get_agent(session, tenant.id, agent_id, lock=True)
    channel = await store.channel(session, agent)
    if channel:
        channel.active = False
        instance = await repo.get_instance(session, channel.instance_id, tenant.id)
        instance.status = "paused"
    await session.commit()
    return await service.detail(session, agent)
