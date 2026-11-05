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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.config import settings
from app.db.models import AgentInstance, ScheduleOccurrence, Template, WorkflowAgent
from app.kernel.definitions import bundles_dir, workflow_from
from app.kernel.procedures import judgment_fields, procedure_of
from app.kernel.workflows import value_at
from app.workflow_agents import service as cases


class NotRunnable(Exception):
    """This occurrence must not run; the reason is shown on it."""


class TargetNotFound(NotRunnable):
    """The agent does not exist in this workspace (a 404 to a request)."""


class Busy(NotRunnable):
    """The agent is still running from an earlier time; this time is skipped, not failed."""


@dataclass(frozen=True)
class Started:
    run_ref: str  # what it started, e.g. "case:<id>"
    outcome: str | None  # how that run came out, when it finished within the occurrence
    # False for work that goes on after the occurrence is committed (an import); the run it
    # names carries its outcome.
    finished: bool = True
    # Called once the occurrence and the run's record are committed: starts the background work.
    after_commit: Callable[[], None] | None = None


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

    async def key(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        """What it is an agent of: the workflow's key, or the runbook template's name."""
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

    async def recover(self, session: AsyncSession, now: datetime) -> None:
        """Before each tick: repair what a worker that died left behind."""
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

    async def key(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        agent = await repo.get_workflow_agent(session, target_id, tenant_id)
        return agent.workflow_key if agent else None

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

    async def recover(self, session: AsyncSession, now: datetime) -> None:
        return None  # a case runs inside its occurrence's transaction: nothing outlives a crash


# --- runbooks: the calendar import and the availability mirror ------------------------------

# How far ahead a run looks, from the day it is due. 70 is the mirror's whole booking horizon
# (app/sync/mirror.MAX_DAYS); a run never looks further than the orchestrator would sweep.
RUNBOOK_MAX_DAYS = 70
RUNBOOK_DEFAULT_DAYS = 8
# A job still `running` with no progress for this long belongs to a worker that is gone
# (ADR-0010 D5).
STALE_IMPORT_MINUTES = 30
_PRACTICE_TZ = ZoneInfo("Europe/Berlin")
_RUNBOOK_INPUT = {
    "type": "object",
    "properties": {
        "days": {
            "type": "integer",
            "title": f"Days ahead it covers, from the day it runs (1–{RUNBOOK_MAX_DAYS})",
            "default": RUNBOOK_DEFAULT_DAYS,
        }
    },
}


# The imports this process started and that may still run: a stopping worker waits for them.
LIVE_IMPORTS: dict[uuid.UUID, Any] = {}


def _spawn(job_id: uuid.UUID, instance_id: uuid.UUID, tenant_id: uuid.UUID, window: dict) -> None:
    from app.sync.jobs import spawn_import_job  # the background worker the button uses

    for done in [j for j, t in LIVE_IMPORTS.items() if not t.is_alive()]:
        del LIVE_IMPORTS[done]
    LIVE_IMPORTS[job_id] = spawn_import_job(job_id, instance_id, tenant_id, window)


def runbook_window(days: int, due: datetime) -> dict[str, str]:
    """The days a run covers, computed when it fires: a stored window is a fact about the day it
    was typed, the booking horizon is a fact about today (ADR-0010 D4)."""
    first: date = due.astimezone(_PRACTICE_TZ).date()
    return {"from": first.isoformat(), "to": (first + timedelta(days=days - 1)).isoformat()}


class RunbookTarget:
    """A deployed calendar import or availability mirror (ADR-0004, ADR-0009): each occurrence is
    one import job, exactly as the Run button starts one, through the same governed contract
    (precondition → allowlist → execute → postcondition → trace) for every appointment or day.
    The schedule decides when and over which days; the contract decides what may be written.

    The target is one deployment, pinned like a version: its template version, settings and
    connections never change, so `version` is the deployment itself."""

    @staticmethod
    def _schedulable_templates() -> frozenset[str]:
        from app.sync.jobs import SCHEDULABLE_TEMPLATES

        return SCHEDULABLE_TEMPLATES

    async def _instance(self, session, tenant_id, target_id) -> tuple[AgentInstance, Template]:
        instance = await repo.get_instance(session, target_id, tenant_id)
        if instance is None:
            raise TargetNotFound("the deployment no longer exists in this workspace")
        template = await session.get(Template, instance.template_id)
        if template is None or template.name not in self._schedulable_templates():
            raise NotRunnable("only a calendar import or an availability mirror runs on a schedule")
        return instance, template

    async def name(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        try:
            _, template = await self._instance(session, tenant_id, target_id)
        except NotRunnable:
            return None
        return template.name.replace("_", " ").capitalize()

    async def key(self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID) -> str | None:
        try:
            _, template = await self._instance(session, tenant_id, target_id)
        except NotRunnable:
            return None
        return template.name

    async def latest_version(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID
    ) -> Version | None:
        instance, _ = await self._instance(session, tenant_id, target_id)
        return Version(instance.id, instance.template_version)

    async def resolve(
        self, session: AsyncSession, tenant_id: uuid.UUID, target_id: uuid.UUID, version_id: uuid.UUID | None
    ) -> Resolved:
        instance, template = await self._instance(session, tenant_id, target_id)
        if version_id is not None and version_id != instance.id:
            raise NotRunnable("a deployment runs only itself")
        if instance.status != "deployed":
            raise NotRunnable(f"this deployment is {instance.status}, not deployed")
        return Resolved(
            template.name.replace("_", " ").capitalize(),
            Version(instance.id, instance.template_version), instance, template,
        )

    def input_schema(self, resolved: Resolved) -> dict | None:
        return copy.deepcopy(_RUNBOOK_INPUT)

    def check_input(self, resolved: Resolved, input: dict) -> None:
        if set(input) - {"days"}:
            raise NotRunnable(f"a scheduled import takes only 'days', not {sorted(set(input) - {'days'})}")
        days = input.get("days", RUNBOOK_DEFAULT_DAYS)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= RUNBOOK_MAX_DAYS:
            raise NotRunnable(f"days is a whole number from 1 to {RUNBOOK_MAX_DAYS}")

    async def start(self, session: AsyncSession, occurrence: ScheduleOccurrence, title: str) -> Started:
        resolved = await self.resolve(
            session, occurrence.tenant_id, occurrence.target_id, occurrence.target_version_id
        )
        self.check_input(resolved, dict(occurrence.input))
        instance: AgentInstance = resolved.agent
        template: Template = resolved.runnable
        # One run of this runbook in the workspace at a time, from any deployment or the button:
        # the lock holds until this transaction commits the new job, so whoever comes next sees
        # it running (ADR-0040 D9).
        await repo.lock_runbook(session, instance.tenant_id, template.name)
        if await repo.running_import_job_for_template(session, instance.tenant_id, template.name):
            raise Busy(f"a {template.name.replace('_', ' ')} was still running; this time was skipped")
        job = await repo.create_import_job(
            session, tenant_id=instance.tenant_id, instance_id=instance.id,
            shadow_task=settings.task_shadow_enabled, trigger="schedule", commit=False,
        )
        window = runbook_window(int(occurrence.input.get("days", RUNBOOK_DEFAULT_DAYS)), occurrence.scheduled_for)
        job_id, instance_id, tenant_id = job.id, instance.id, instance.tenant_id
        return Started(
            run_ref=f"import_job:{job_id}", outcome=None, finished=False,
            after_commit=lambda: _spawn(job_id, instance_id, tenant_id, window),
        )

    async def recover(self, session: AsyncSession, now: datetime) -> None:
        await repo.reclaim_stale_import_jobs(session, older_than_minutes=STALE_IMPORT_MINUTES)


TARGETS: dict[str, Target] = {"workflow": WorkflowCaseTarget(), "runbook": RunbookTarget()}


def target_for(kind: str) -> Target:
    try:
        return TARGETS[kind]
    except KeyError:
        raise NotRunnable(f"nothing runs schedules of kind {kind!r}") from None
