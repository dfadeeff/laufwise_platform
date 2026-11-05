"""What an occurrence runs (ADR-0040 D3): one dispatch target per runtime. The clock knows no
domain; each target starts its runtime's ordinary governed run, the same one a person starts.

A target raises `NotRunnable` for anything that makes the occurrence one that must not run: the
agent is gone or another workspace's, the version is not published, the workflow does not allow
a schedule, or the input is not one a person could have saved. The same checks run when a
schedule is saved (`resolve`, `check_input`) and when it fires (`start`), so what is refused at
fire time was refused at save time too, unless something changed in between; then the worker
records it as failed.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import ScheduleOccurrence, WorkflowAgent
from app.kernel.definitions import bundles_dir, workflow_from
from app.kernel.procedures import judgment_fields, procedure_of
from app.kernel.workflows import value_at
from app.workflow_agents import service as cases


class NotRunnable(Exception):
    """This occurrence must not run; the reason is shown on it."""


class TargetNotFound(NotRunnable):
    """The agent does not exist in this workspace (a 404 to a request)."""


@dataclass(frozen=True)
class Started:
    run_ref: str  # what it started, e.g. "case:<id>"
    outcome: str | None  # how that run came out, when it finished within the occurrence


@dataclass(frozen=True)
class Version:
    id: uuid.UUID
    number: int


@dataclass(frozen=True)
class Resolved:
    """A target that may run: its agent's name, the version, and what the runtime needs. Read
    from what is stored: resolving builds nothing, so the Studio may ask on every page view."""

    name: str
    version: Version
    agent: Any
    runnable: Any
    owner: str | None = None
    gate: dict | None = None


class Target(Protocol):
    async def name(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        """The agent's name, or None if it no longer exists in this workspace."""
        ...

    async def latest_version(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID
    ) -> Version | None:
        """The newest published version the agent could run."""
        ...

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID, version_id: uuid.UUID
    ) -> Resolved:
        """The target at that version, or NotRunnable / TargetNotFound."""
        ...

    def check_input(self, resolved: Resolved, input: dict) -> None:
        """NotRunnable if a person could not have saved this input."""
        ...

    def input_schema(self, resolved: Resolved) -> dict | None:
        """The form the Studio fills a schedule's input with."""
        ...

    async def start(self, session: AsyncSession, occurrence: ScheduleOccurrence, title: str) -> Started:
        """Start the run in `session`; the worker commits it with the occurrence, or not at all."""
        ...


class WorkflowCaseTarget:
    """A workflow agent (ADR-0030/0039): each occurrence is a new case, with the schedule's input
    saved as a person would save it, computed on the schedule's published version. A case only
    computes; anything it sends waits for a person's approval (ADR-0035)."""

    async def _agent(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> WorkflowAgent:
        agent = await repo.get_workflow_agent(session, target_id, tenant_id)
        if agent is None:
            raise TargetNotFound("the agent no longer exists in this workspace")
        return agent

    async def name(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        agent = await repo.get_workflow_agent(session, target_id, tenant_id)
        return agent.name if agent else None

    async def latest_version(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID
    ) -> Version | None:
        agent = await self._agent(session, tenant_id, target_id)
        version = await repo.latest_published_version(session, agent.workflow_key, tenant_id)
        return Version(version.id, version.version) if version else None

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID, version_id: uuid.UUID | None
    ) -> Resolved:
        agent = await self._agent(session, tenant_id, target_id)
        if version_id is None:
            raise NotRunnable("no published version is set to run")
        found = await repo.published_version_of(session, version_id, agent.workflow_key, tenant_id)
        if found is None:
            raise NotRunnable("the version set to run is not a published version of this agent's workflow")
        definition, version = found
        if definition.module_key not in await repo.workspace_module_keys(session, tenant_id):
            raise NotRunnable(f"this workspace no longer runs the {definition.module_key} module")
        if "schedule" not in (version.content.get("triggers") or ["manual"]):
            raise NotRunnable("this workflow cannot be scheduled: its definition allows manual runs only")
        return Resolved(
            agent.name, Version(version.id, version.version), agent, version.content,
            owner=str(definition.tenant_id) if definition.tenant_id else None, gate=version.gate,
        )

    def input_schema(self, resolved: Resolved) -> dict | None:
        return copy.deepcopy((resolved.runnable.get("forms") or {}).get("input"))

    def check_input(self, resolved: Resolved, input: dict) -> None:
        for name in sorted(judgment_fields(procedure_of(resolved.runnable))):
            if value_at(input, name) is not None:
                raise NotRunnable(f"{name} is a person's judgment: decide it in the case, with a reason")

    async def start(self, session: AsyncSession, occurrence: ScheduleOccurrence, title: str) -> Started:
        resolved = await self.resolve(
            session, occurrence.tenant_id, occurrence.target_id, occurrence.target_version_id
        )
        agent = resolved.agent
        # Only a run builds the workflow's bundle; resolving reads what is stored.
        workflow = workflow_from(
            resolved.runnable, bundles_dir(), version_id=str(resolved.version.id),
            gate=resolved.gate, owner=resolved.owner,
        )
        case = cases.new_case(session, agent, workflow, title)
        await cases.created(session, case)
        try:
            cases.save_input(
                session, case, case.input_revision, dict(occurrence.input),
                judgments=judgment_fields(workflow.procedure),
            )
        except cases.JudgmentInInput as exc:
            raise NotRunnable(str(exc)) from exc
        cases.compute(session, case, agent, workflow, trigger="schedule")
        return Started(run_ref=f"case:{case.id}", outcome=case.status)


TARGETS: dict[str, Target] = {"workflow": WorkflowCaseTarget()}


def target_for(kind: str) -> Target:
    try:
        return TARGETS[kind]
    except KeyError:
        raise NotRunnable(f"nothing runs schedules of kind {kind!r}") from None
