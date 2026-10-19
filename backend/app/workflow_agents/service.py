"""Creating workflow agents, and moving their cases from input to result (ADR-0030) — no FastAPI.

The rule this file exists to keep: a result is only ever the output of computing the current
input, and it is only shown if the workflow's own check accepts it. Saving an input clears the
result; nothing here can write one directly. Every save and every computation is appended to the
case's history, with what it produced.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    StoredFile,
    WorkflowAgent,
    WorkflowCase,
    WorkflowCaseEvent,
    WorkflowCaseFile,
)
from app.kernel.schema import empty
from app.kernel.workflows import InputError, Workflow


class StaleInput(Exception):
    """The input was saved by someone else since it was read; nothing was overwritten."""


class UnconfirmedInput(Exception):
    """A model read this input and no person has confirmed it yet; it is not computed."""


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


def save_input(session: AsyncSession, case: WorkflowCase, revision: int, data: dict) -> None:
    """Replace the input. `revision` must be the one the editor read, so two people editing the
    same case cannot silently overwrite each other."""
    if revision != case.input_revision:
        raise StaleInput(
            f"the input changed since it was opened (revision {case.input_revision}, "
            f"not {revision}); reload it"
        )
    case.input = data
    case.input_revision += 1
    # A person saved it: whatever a model read before is now theirs, checked or corrected.
    case.input_source = "person"
    case.to_check = []
    case.status = "draft"
    case.result = None
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
) -> None:
    """Replace the input with what a model read from the documents. It stays unconfirmed, and is
    not computed, until a person saves it."""
    case.input = data
    case.input_revision += 1
    case.input_source = "extracted"
    case.to_check = to_check
    case.status = "draft"
    case.result = None
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
    session: AsyncSession, case: WorkflowCase, agent: WorkflowAgent, workflow: Workflow
) -> None:
    if case.input_source == "extracted":
        raise UnconfirmedInput(
            "a model read this input from the documents; check it and save it before it is computed"
        )
    case.computed_revision = case.input_revision
    case.rules_version = workflow.rules_version
    case.result = None
    case.errors, case.questions, case.assumptions, case.problems = [], [], [], []
    try:
        parsed = workflow.parse(case.input, agent.settings or {})
    except InputError as exc:
        case.status = "draft"
        case.errors = list(exc.messages)
    else:
        computed = workflow.compute(parsed)
        case.assumptions = list(computed.assumptions)
        if computed.questions:
            case.status = "blocked"
            case.questions = list(computed.questions)
        else:
            # The postcondition. Run here, by the kernel, on every result: a module cannot skip
            # it, and a result it refuses is never stored as one.
            problems = workflow.check(parsed, computed.value)
            if problems:
                case.status = "failed"
                case.problems = list(problems)
            else:
                case.status = "ready"
                case.result = computed.result
    _event(
        session,
        case,
        "computed",
        revision=case.input_revision,
        rules_version=workflow.rules_version,
        # Exactly what it was computed from, kept with it (red flag C4): the next save replaces
        # `case.input`, and the agent's settings can change, but this record cannot.
        input=case.input,
        settings=agent.settings or {},
        status=case.status,
        result=case.result,
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
