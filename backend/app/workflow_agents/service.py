"""Creating workflow agents, and moving their cases from input to result (ADR-0030) — no FastAPI.

The rule this file exists to keep: a result is only ever the output of computing the current
input, and it is only shown if the workflow's own check accepts it. Saving an input clears the
result; nothing here can write one directly. Every save and every computation is appended to the
case's history, with what it produced.

A case can wait for facts (ADR-0039 step 3b), or for a person's judgment (step 3c). Entering
them is the one change that keeps its run: the run resumes on its own version. Any other change
to the input ends the held run. A judgment is only ever entered as a decision, by a person, with
a reason: the input form may withdraw one, never set it, and a model reading documents never
fills one.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service as approvals
from app.db import repo
from app.db.models import (
    CaseRun,
    StoredFile,
    WorkflowAgent,
    WorkflowCase,
    WorkflowCaseEvent,
    WorkflowCaseFile,
)
from app.definitions import service as definitions
from app.kernel.schema import empty
from app.kernel.workflows import Workflow, field_schema, value_at, with_value
from app.workflow_agents import runs


log = logging.getLogger(__name__)
EXPIRY_SWEEP_SECONDS = 3600


class StaleInput(Exception):
    """The input was saved by someone else since it was read; nothing was overwritten."""


class UnconfirmedInput(Exception):
    """A model read this input and no person has confirmed it yet; it is not computed."""


class NotAwaited(Exception):
    """The case is not waiting for this fact or decision; nothing was changed."""


class JudgmentInInput(Exception):
    """A person's judgment was set through the input; it is taken as a decision, with a reason."""


def _event(session: AsyncSession, case: WorkflowCase, kind: str, **payload) -> None:
    session.add(WorkflowCaseEvent(case_id=case.id, kind=kind, payload=payload))


def new_agent(tenant_id, module_key: str, workflow: Workflow, name: str) -> WorkflowAgent:
    return WorkflowAgent(
        tenant_id=tenant_id,
        module_key=module_key,
        workflow_key=workflow.key,
        name=name,
        settings=empty(workflow.schemas()["settings"]),
    )


def new_case(session: AsyncSession, agent: WorkflowAgent, workflow: Workflow, title: str) -> WorkflowCase:
    case = WorkflowCase(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        title=title,
        status="draft",
        input=empty(workflow.schemas()["input"]),
        input_revision=1,
        input_source="person",
        to_check=[],
        drafts=[],
        errors=[],
        questions=[],
        assumptions=[],
        problems=[],
    )
    session.add(case)
    return case


async def created(session: AsyncSession, case: WorkflowCase) -> None:
    await session.flush()
    _event(session, case, "created")


def _fresh(case: WorkflowCase, revision: int) -> None:
    if revision != case.input_revision:
        raise StaleInput(
            f"the input changed since it was opened (revision {case.input_revision}, "
            f"not {revision}); reload it"
        )


def save_input(
    session: AsyncSession,
    case: WorkflowCase,
    revision: int,
    data: dict,
    run: CaseRun | None = None,
    judgments: set[str] = frozenset(),
) -> None:
    """Replace the input. `revision` must be the one the editor read, so two people editing the
    same case cannot silently overwrite each other. `run` is the case's latest run: held at a
    wait, it ends, since it waited on an input that is no longer the case's. `judgments` are the
    fields only a decision sets: the save may keep or clear one, not change it."""
    _fresh(case, revision)
    for name in sorted(judgments):
        if value_at(data, name) is not None and value_at(data, name) != value_at(case.input, name):
            raise JudgmentInInput(f"{name} is a person's judgment: decide it in the case, with a reason")
    runs.cancel(run)
    case.input = data
    case.input_revision += 1
    # A person saved it: whatever a model read before is now theirs, checked or corrected.
    case.input_source = "person"
    case.to_check = []
    case.status = "draft"
    case.result = None
    case.drafts = []
    case.computed_revision = None
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []
    _event(session, case, "input_saved", revision=case.input_revision)


def apply_extraction(
    session: AsyncSession,
    case: WorkflowCase,
    data: dict,
    to_check: list[str],
    model: str,
    files: list[StoredFile],
    run: CaseRun | None = None,
    judgments: set[str] = frozenset(),
) -> None:
    """Replace the input with what a model read from the documents. It stays unconfirmed, and is
    not computed, until a person saves it. A judgment it put in is taken out again: that is a
    person's to make."""
    for name in judgments:
        if value_at(data, name) is not None:
            data = with_value(data, name, None)
    runs.cancel(run)
    case.input = data
    case.input_revision += 1
    case.input_source = "extracted"
    case.to_check = to_check
    case.status = "draft"
    case.result = None
    case.drafts = []
    case.computed_revision = None
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []
    _event(
        session,
        case,
        "extracted",
        revision=case.input_revision,
        model=model,
        files=[f.name for f in files],
        to_check=to_check,
    )


def compute(
    session: AsyncSession,
    case: WorkflowCase,
    agent: WorkflowAgent,
    workflow: Workflow,
    previous: CaseRun | None = None,
    trigger: str = "manual",
) -> None:
    """Run the case from the start on the newest published version. A run still held at a wait
    (`previous`) ends: this one replaces it. `trigger` says who started it: a person, or a
    schedule (ADR-0040)."""
    if case.input_source == "extracted":
        raise UnconfirmedInput(
            "a model read this input from the documents; check it and save it before it is computed"
        )
    runs.cancel(previous)
    case.computed_revision = case.input_revision
    case.rules_version = workflow.rules_version
    case.definition_version_id = uuid.UUID(workflow.version_id) if workflow.version_id else None
    run = runs.run_case(case, agent, workflow)
    run.trigger = trigger
    session.add(run)
    _computed(session, case, agent, workflow, run, "computed")


async def supply_facts(
    session: AsyncSession,
    case: WorkflowCase,
    agent: WorkflowAgent,
    current: Workflow,
    run: CaseRun | None,
    revision: int,
    facts: dict[str, Any],
) -> None:
    """Enter facts a held run waits for, as the party named sent them, and resume it once none
    is missing. The run goes on with the version it started on, and offers no letter again that
    was already sent."""
    _fresh(case, revision)
    awaited = {w["fact"]: w for w in runs.awaited(run)}
    if not facts or not set(facts) <= set(awaited):
        raise NotAwaited(f"this case waits for {sorted(awaited) or 'nothing'}, not {sorted(facts)}")
    for fact, value in facts.items():
        if value is None or value == "":
            raise NotAwaited(f"{fact}: enter the value {awaited[fact]['from']} sent")
    received = {f: {"value": v, "from": awaited[f]["from"]} for f, v in facts.items()}
    await _enter(session, case, agent, current, run, facts, set(awaited), "facts_received", facts=received)


async def decide(
    session: AsyncSession,
    case: WorkflowCase,
    agent: WorkflowAgent,
    current: Workflow,
    run: CaseRun | None,
    revision: int,
    decisions: dict[str, dict],
    by: str,
) -> None:
    """Take judgments a held run waits for: each one of the answers its decide step allows,
    with the reason it was taken, recorded with who took it. Once none is left, the function
    computes again with them in, on the version the run started on."""
    _fresh(case, revision)
    asked = {d["field"]: d for d in runs.to_decide(run)}
    if not decisions or not set(decisions) <= set(asked):
        raise NotAwaited(f"this case waits for a decision on {sorted(asked) or 'nothing'}, not {sorted(decisions)}")
    taken = {}
    for name, d in decisions.items():
        allowed = [o["value"] for o in asked[name]["options"]]
        if d.get("value") not in allowed:
            raise NotAwaited(f"{name}: decide one of {allowed}")
        if not str(d.get("reason") or "").strip():
            raise NotAwaited(f"{name}: a decision states its reason")
        taken[name] = {"value": d["value"], "reason": d["reason"].strip(), "by": by}
    await _enter(
        session, case, agent, current, run,
        {k: v["value"] for k, v in taken.items()}, set(asked), "decided", decisions=taken,
    )


async def _enter(
    session: AsyncSession,
    case: WorkflowCase,
    agent: WorkflowAgent,
    current: Workflow,
    run: CaseRun,
    values: dict[str, Any],
    held_for: set[str],
    partial_event: str,
    **record,
) -> None:
    """Put what a held run waited for into the input, and resume it once nothing is missing."""
    workflow = await definitions.workflow_of_version(session, run.definition_version_id, current)
    data = case.input
    for name, value in values.items():
        if field_schema(workflow.forms["input"], name) is None:
            raise NotAwaited(f"{name}: the input has no such field")
        try:
            data = with_value(data, name, value)
        except (KeyError, IndexError, ValueError):
            raise NotAwaited(f"{name}: the input has no place for it") from None
    # Before the case changes: which of the letters it shows were sent (the approval that sent
    # them describes the case only as it is now).
    history = await repo.case_approvals(session, case.id, case.tenant_id)
    sent = approvals.sent_letters(case, history)
    approvals.withdraw_pending(session, case, history)
    case.input = data
    case.input_revision += 1
    case.input_source = "person"
    case.to_check = []
    # One event per request: events of one transaction share its timestamp, so two would have
    # no order between them.
    if any(value_at(data, name) is None for name in held_for):
        _event(session, case, partial_event, revision=case.input_revision, **record)
        return  # still held: the rest has not arrived
    case.computed_revision = case.input_revision
    runs.resume_run(case, agent, workflow, run, sent)
    _computed(session, case, agent, workflow, run, "resumed", **record)


async def expire_waits(session: AsyncSession, now: datetime | None = None) -> int:
    """End every run whose wait went unattended past its date; a person decides what now."""
    ended = 0
    for run in await repo.expired_waits(session, now or datetime.now(timezone.utc)):
        case = await repo.get_workflow_case(session, run.case_id, run.tenant_id, lock=True)
        await session.refresh(run)  # under the case's lock: a person may have resumed it since
        if run.state != "suspended":
            continue
        latest = await repo.latest_case_run(session, run.case_id)
        if latest is None or latest.id != run.id:
            runs.cancel(run)  # a newer run replaced it
            continue
        approvals.withdraw_pending(session, case, await repo.case_approvals(session, case.id, case.tenant_id))
        runs.expire(case, run)
        _event(session, case, "wait_expired", run_id=str(run.id), status=case.status, questions=case.questions)
        ended += 1
    return ended


async def run_wait_expiry(sessionmaker) -> None:
    """End unattended waits once an hour, for as long as the process runs: a held run that nobody
    attends must not wait forever, and a promise kept by the running service cannot lapse."""
    while True:
        try:
            async with sessionmaker() as session:
                if ended := await expire_waits(session):
                    log.info("ended %d unattended waits", ended)
                await session.commit()
        except Exception:  # the loop must outlive one bad hour
            log.exception("wait expiry sweep failed")
        await asyncio.sleep(EXPIRY_SWEEP_SECONDS)


def _computed(
    session: AsyncSession,
    case: WorkflowCase,
    agent: WorkflowAgent,
    workflow: Workflow,
    run: CaseRun,
    kind: str,
    **extra,
) -> None:
    _event(
        session,
        case,
        kind,
        **extra,
        revision=case.input_revision,
        rules_version=workflow.rules_version,
        run_id=str(run.id),
        # The exact version that computed it: published versions never change (ADR-0039 2b).
        definition=workflow.key,
        definition_version=workflow.version,
        definition_version_id=workflow.version_id,
        # Exactly what it was computed from, kept with it (red flag C4): the next save replaces
        # `case.input`, and the agent's settings can change, but this record cannot.
        input=case.input,
        settings=agent.settings or {},
        status=case.status,
        result=case.result,
        drafts=case.drafts,
        errors=case.errors,
        questions=case.questions,
        assumptions=case.assumptions,
        problems=case.problems,
    )


def attach(session: AsyncSession, case: WorkflowCase, file: StoredFile) -> None:
    session.add(WorkflowCaseFile(case_id=case.id, file_id=file.id))
    _event(session, case, "file_attached", file_id=str(file.id), name=file.name)


async def detach(session: AsyncSession, case: WorkflowCase, file: StoredFile) -> None:
    link = await session.get(WorkflowCaseFile, (case.id, file.id))
    if link is not None:
        await session.delete(link)
        _event(session, case, "file_detached", file_id=str(file.id), name=file.name)
