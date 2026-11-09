"""Facade over the laufwise control-plane primitive.

This is the ONE place the platform binds the *execution* of a governed run to the engine.
Everything above it depends on this facade, not on laufwise directly — so the engine can be
swapped (LocalEngine -> TemporalEngine) without touching the rest of the app.

Stage 2: the contract is resolved from the `Template` table and each run is persisted (a `Run`
row + ordered `EpisodeEvent` rows) before the result is returned. The pure engine loop lives in
`runner.execute_contract`; this facade wraps it with resolution + persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connections.resolve import resolve_connectors
from app.control_plane.runner import ExecResult, execute_contract
from app.core.errors import NotFoundError
from app.db import repo
from app.db.models import AgentInstance, Template
from app.db.session import get_sessionmaker
from app.workloads.demo_tools import DEMO_TOOLS
from app.schemas.run import RunRequest, RunResult
from app.templates.contract import TemplateContract


class Runtime:
    """Resolves a published template and drives + persists a run through the contract loop."""

    def __init__(self, runs_dir: str) -> None:
        self._runs_dir = runs_dir

    async def run(
        self, session: AsyncSession, request: RunRequest, *, tenant_id: uuid.UUID | None = None
    ) -> RunResult:
        template = await repo.latest_published_template(session, request.runbook)
        if template is None:
            raise NotFoundError(f"no published template '{request.runbook}'")

        contract = TemplateContract.model_validate(template.contract)
        result = await self.execute_recorded(session, contract, request.case, tenant_id=tenant_id)
        return self._response(result)

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
        # Resolve the instance's bound connections into real providers + tools; a thevea-bound
        # calendar routes to the live API, while unbound roles fall back to the case fixture.
        connectors = await resolve_connectors(session, instance, case)
        try:
            result = await self.execute_recorded(
                session, contract, {**case, "param_values": instance.param_values},
                tenant_id=instance.tenant_id, instance_id=instance.id,
                providers=connectors.providers, tools=connectors.tools,
            )
            return self._response(result)
        finally:
            connectors.close()

    @staticmethod
    def _response(result: ExecResult) -> RunResult:
        return RunResult(run_id=result.run_id, runbook=result.runbook, steps=result.steps, trace_path=None)

    async def execute_recorded(
        self, session, contract, case, *, tenant_id, instance_id=None, providers=None, tools=None,
    ) -> ExecResult:
        run_id = uuid.uuid4()
        await repo.begin_run(
            session, run_id=run_id, template_name=contract.name, template_version=contract.version,
            tenant_id=tenant_id, instance_id=instance_id, case=case,
        )
        loop = asyncio.get_running_loop()
        sessionmaker = get_sessionmaker()

        async def append(kind, payload):
            async with sessionmaker() as journal:
                await repo.append_run_event(
                    journal, run_id=run_id, tenant_id=tenant_id, kind=kind, payload=payload,
                )

        def record(kind, payload):
            # The synchronous engine runs in a thread; each journal write commits independently.
            # A failed commit raises BEFORE a tool acts or the next step starts.
            future = asyncio.run_coroutine_threadsafe(append(kind, payload), loop)
            try:
                future.result(timeout=30)
            except BaseException:
                future.cancel()
                raise

        class DurableTrace:
            def event(self, **fields):
                record("trace", {
                    "ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id.hex,
                    "runbook": contract.name, "version": contract.version, **fields,
                })

            def close(self):
                pass

        def wrapped(name, implementation):
            calls = 0
            def act(provider, step):
                nonlocal calls
                calls += 1
                intent = {
                    "action_key": f"{run_id.hex}:{step.id}:{calls}",
                    "step_id": step.id, "tool": name,
                    "args_sha256": hashlib.sha256(json.dumps(
                        step.execute.args, sort_keys=True, default=str,
                    ).encode()).hexdigest(),
                }
                record("action_intent", intent)
                outcome = implementation(provider, step)
                # A return is a claim; the engine's independently journaled trace is the ruling.
                record("action_returned", {**intent, "ok": getattr(outcome, "ok", True)})
                return outcome
            return act

        registered = {name: wrapped(name, impl) for name, impl in {**DEMO_TOOLS, **(tools or {})}.items()}
        task = asyncio.create_task(asyncio.to_thread(
            execute_contract, contract, case, self._runs_dir, run_id=run_id.hex,
            real_providers=providers or None, extra_tools=registered, trace_sink=DurableTrace(),
        ))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not close a live connector or abandon its journal while its thread can act.
            await task
            raise
        await repo.complete_run(
            session, run_id=run_id, tenant_id=tenant_id, status=result.status,
            trace_ref=f"db:run:{run_id.hex}", step_payloads=[s.model_dump() for s in result.steps],
        )
        return replace(result, trace_path=f"db:run:{run_id.hex}")
