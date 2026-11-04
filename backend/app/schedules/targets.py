"""What an occurrence runs (ADR-0040 D3): one dispatch target per runtime. The clock knows no
domain; each target starts its runtime's ordinary governed run, the same one a person starts.

A target raises `NotRunnable` for anything that makes the occurrence one that must not run: the
agent is gone or another workspace's, the version is not published, the workflow does not allow
a schedule, or the input is not one a person could have saved. The worker records it as failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import ScheduleOccurrence
from app.kernel.definitions import bundles_dir, workflow_from
from app.kernel.procedures import judgment_fields
from app.workflow_agents import service as cases


class NotRunnable(Exception):
    """This occurrence must not run; the reason is shown on it."""


@dataclass(frozen=True)
class Started:
    run_ref: str  # what it started, e.g. "case:<id>"
    outcome: str | None  # how that run came out, when it finished within the occurrence


class Target(Protocol):
    async def start(self, session: AsyncSession, occurrence: ScheduleOccurrence, title: str) -> Started:
        """Start the run in `session`; the worker commits it with the occurrence, or not at all."""
        ...


class WorkflowCaseTarget:
    """A workflow agent (ADR-0030/0039): each occurrence is a new case, with the schedule's input
    saved as a person would save it, computed on the schedule's published version. A case only
    computes; anything it sends waits for a person's approval (ADR-0035)."""

    async def start(self, session: AsyncSession, occurrence: ScheduleOccurrence, title: str) -> Started:
        tenant_id = occurrence.tenant_id
        agent = await repo.get_workflow_agent(session, occurrence.target_id, tenant_id)
        if agent is None:
            raise NotRunnable("the agent no longer exists in this workspace")
        if occurrence.target_version_id is None:
            raise NotRunnable("no published version is set to run")
        found = await repo.published_version_of(
            session, occurrence.target_version_id, agent.workflow_key, tenant_id
        )
        if found is None:
            raise NotRunnable("the version set to run is not a published version of this agent's workflow")
        definition, version = found
        if definition.module_key not in await repo.workspace_module_keys(session, tenant_id):
            raise NotRunnable(f"this workspace no longer runs the {definition.module_key} module")
        workflow = workflow_from(
            version.content, bundles_dir(), version_id=str(version.id), gate=version.gate,
            owner=str(definition.tenant_id) if definition.tenant_id else None,
        )
        if not workflow.schedulable:
            raise NotRunnable("this workflow cannot be scheduled: its definition allows manual runs only")

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
