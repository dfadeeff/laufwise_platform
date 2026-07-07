"""Facade over the laufwise control-plane primitive.

This is the ONE place the platform binds the *execution* of a governed run to the engine.
Everything above it depends on this facade, not on laufwise directly — so the engine can be
swapped (LocalEngine -> TemporalEngine) without touching the rest of the app.

Stage 2: the contract is resolved from the `Template` table and each run is persisted (a `Run`
row + ordered `EpisodeEvent` rows) before the result is returned. The pure engine loop lives in
`runner.execute_contract`; this facade wraps it with resolution + persistence.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.control_plane.runner import execute_contract
from app.core.errors import NotFoundError
from app.db import repo
from app.db.models import AgentInstance, Template
from app.schemas.run import RunRequest, RunResult
from app.templates.contract import TemplateContract


class Runtime:
    """Resolves a published template and drives + persists a run through the contract loop."""

    def __init__(self, runs_dir: str) -> None:
        self._runs_dir = runs_dir

    async def run(self, session: AsyncSession, request: RunRequest) -> RunResult:
        template = await repo.latest_published_template(session, request.runbook)
        if template is None:
            raise NotFoundError(f"no published template '{request.runbook}'")

        contract = TemplateContract.model_validate(template.contract)
        result = execute_contract(contract, request.case, self._runs_dir)

        await repo.save_run(
            session,
            run_id=uuid.UUID(result.run_id),
            template_name=result.runbook,
            template_version=result.version,
            status=result.status,
            trace_ref=result.trace_path,
            step_payloads=[s.model_dump() for s in result.steps],
        )
        return RunResult(
            run_id=result.run_id,
            runbook=result.runbook,
            steps=result.steps,
            trace_path=result.trace_path,
        )

    async def run_instance(
        self, session: AsyncSession, instance: AgentInstance, case: dict[str, Any]
    ) -> RunResult:
        """Run a deployed instance (Stage 4): the contract is the *pinned* template@version
        and the parameters are the instance's stored param_values — a run is reproducible
        from the instance row alone, independent of later template versions."""
        template = await session.get(Template, instance.template_id)
        if template is None:
            raise NotFoundError(f"instance {instance.id.hex} references a missing template")

        contract = TemplateContract.model_validate(template.contract)
        result = execute_contract(
            contract, {**case, "param_values": instance.param_values}, self._runs_dir
        )
        await repo.save_run(
            session,
            run_id=uuid.UUID(result.run_id),
            template_name=result.runbook,
            template_version=result.version,
            status=result.status,
            trace_ref=result.trace_path,
            step_payloads=[s.model_dump() for s in result.steps],
            instance_id=instance.id,
        )
        return RunResult(
            run_id=result.run_id,
            runbook=result.runbook,
            steps=result.steps,
            trace_path=result.trace_path,
        )