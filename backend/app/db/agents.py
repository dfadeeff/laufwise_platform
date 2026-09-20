"""Tenant-scoped persistence and locking for Studio; no HTTP or provider calls."""

import uuid
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.models import StudioAgent, AgentInstance, VoiceChannel, InstanceConnection


async def list_agents(session, tenant_id):
    return list(
        (
            await session.scalars(
                select(StudioAgent)
                .where(StudioAgent.tenant_id == tenant_id)
                .order_by(StudioAgent.created_at.desc())
            )
        ).all()
    )


async def get_agent(session, tenant_id, agent_id, *, lock=False):
    query = select(StudioAgent).where(
        StudioAgent.id == agent_id, StudioAgent.tenant_id == tenant_id
    )
    if lock:
        query = query.with_for_update()
    return (await session.scalars(query)).first()


async def create_agent(session, tenant_id, config):
    row = StudioAgent(tenant_id=tenant_id, draft=config, generation=1)
    session.add(row)
    await session.flush()
    return row


async def history(session, agent):
    return list(
        (
            await session.scalars(
                select(AgentInstance)
                .where(
                    AgentInstance.agent_id == agent.id,
                    AgentInstance.tenant_id == agent.tenant_id,
                    AgentInstance.snapshot_kind == "published",
                )
                .order_by(AgentInstance.created_at.desc())
            )
        ).all()
    )


async def snapshot(session, agent, template, config, kind):
    row = AgentInstance(
        id=uuid.uuid4(),
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        template_id=template.id,
        template_version=template.version,
        param_values={"locale": config["locale"]},
        status="draft" if kind == "test" else "paused",
        revision=agent.generation,
        runtime_config=config,
        snapshot_kind=kind,
        connections=[],
    )
    session.add(row)
    await session.flush()
    return row


async def channel(session, agent):
    return (
        await session.scalars(
            select(VoiceChannel).where(
                VoiceChannel.agent_id == agent.id, VoiceChannel.tenant_id == agent.tenant_id
            )
        )
    ).first()


async def number_owner(session, number):
    return (
        await session.scalars(select(VoiceChannel).where(VoiceChannel.phone_number == number))
    ).first()


async def assign_channel(session, agent, instance, connection_id, number):
    row = await channel(session, agent)
    if row and row.instance_id != instance.id:
        old = await session.get(AgentInstance, row.instance_id)
        if old:
            old.status = "paused"
            old.phone_number = None
    if row is None:
        row = VoiceChannel(tenant_id=agent.tenant_id, agent_id=agent.id)
        session.add(row)
    row.instance_id, row.connection_id, row.phone_number, row.active = (
        instance.id,
        connection_id,
        number,
        True,
    )
    instance.phone_number, instance.status = number, "deployed"
    # Bindings are operational; the immutable behavior snapshot is unchanged.
    instance.connections = [InstanceConnection(role="calendar", connection_id=connection_id)]
    await session.flush()
    return row


async def phone_instance(session, number):
    row = (
        await session.scalars(select(VoiceChannel).where(VoiceChannel.phone_number == number))
    ).first()
    if row is None:
        return None, False
    if not row.active:
        return None, True
    instance = (
        await session.scalars(
            select(AgentInstance)
            .where(
                AgentInstance.id == row.instance_id,
                AgentInstance.tenant_id == row.tenant_id,
                AgentInstance.status == "deployed",
            )
            .options(selectinload(AgentInstance.connections))
        )
    ).first()
    return instance, True
