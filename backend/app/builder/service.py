"""Running the builder on a workspace's definition (ADR-0039 D6, step 6): no FastAPI.

A run takes an instruction and documents, and works on the definition's draft (or, if it has
none, on a new draft from its published version):

1. The model answers with a change (`guide.md`); the platform applies it (`apply.py`), checks
   the content, and runs every case through the gate, in the sandbox, as publishing would.
2. If a case fails, the model is shown the failures and answers again, at most `MAX_ROUNDS`
   times. It is told to fix code and rules to fit the norm, never an expected figure to fit
   the code; and every figure it writes is unverified until a person confirms it anyway.
3. The last change that could be applied is saved as the draft, with the run's summary, each
   round's report, and how the guardrail panel's answers changed.

Cases a person hid from the builder (an expert's sheet) are not in what it is shown, and not run
in its rounds: a failure there would hand it the answer. It is told only how many there are. The
person's test run and the publish gate run them like any other.

What the builder cannot do is not a rule it is asked to follow; the capability is absent. It has
no way to publish, to change a shipped template or another workspace's definition, to confirm a
figure, or to run code anywhere but the sandbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.apply import ProposalError, apply, hidden_count, visible
from app.builder.model import BuilderModel, BuilderUnavailable, Document, builder_model
from app.db import repo
from app.db.models import BuilderRun, Definition
from app.definitions import service as definitions
from app.definitions.gate import guardrails, run_gate
from app.files.store import blob_store
from app.kernel.definitions import DefinitionError, bundles_dir, check_content, workflow_from

log = logging.getLogger(__name__)
GUIDE = (Path(__file__).with_name("guide.md")).read_text(encoding="utf-8")
MAX_ROUNDS = 4


class Busy(Exception):
    """The definition already has a builder run in progress."""


async def start(
    session: AsyncSession, definition: Definition, instruction: str, file_ids: list[str], requested_by: str
) -> BuilderRun:
    """Record a run; the caller executes it after committing."""
    if await working(session, definition):
        raise Busy("the builder is already working on this definition")
    for file_id in file_ids:
        stored = await repo.get_stored_file(session, _uuid(file_id), definition.tenant_id)
        if stored is None:
            raise DefinitionError(f"no such file {file_id}")
    run = BuilderRun(
        tenant_id=definition.tenant_id,
        definition_id=definition.id,
        requested_by=requested_by,
        instruction=instruction,
        file_ids=file_ids,
        state="queued",
        rounds=[],
        guardrail_changes=[],
    )
    session.add(run)
    await session.commit()
    return run


async def working(session: AsyncSession, definition: Definition) -> bool:
    """A run is queued or running: it saves the draft when it ends, over whatever is there."""
    return any(r.state in ("queued", "running") for r in await repo.builder_runs(session, definition.id))


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.UUID(int=0)  # no file has it: refused as "no such file"


def _passes(report: dict) -> bool:
    """Every case passed. Unconfirmed figures still keep it from being published, but they are a
    person's to confirm, not the builder's to fix."""
    return all(c["passed"] for c in report["cases"]) and (not report.get("refused") or bool(report.get("unverified")))


def _failures(report: dict) -> str:
    lines = [report["refused"]] if report.get("refused") and not report.get("unverified") else []
    lines += [f"case {c['name']}: {f}" for c in report["cases"] for f in c["failures"]]
    return "\n".join(f"- {line}" for line in lines)


def _panel(content: dict) -> dict[str, str]:
    try:
        return {g["question"]: g["answer"] for g in guardrails(workflow_from(content, bundles_dir()))}
    except DefinitionError:
        return {}


def _first_turn(run: BuilderRun, content: dict, documents: list[Document]) -> dict:
    editable = {k: v for k, v in visible(content).items() if k not in ("key", "module", "version")}
    held = hidden_count(content)
    return {
        "role": "user",
        "text": (
            f"The instruction:\n{run.instruction}\n\n"
            f"The definition as it is now, as JSON:\n{json.dumps(editable, ensure_ascii=False)}\n\n"
            + (
                f"A person holds back {held} more test case{'s' if held != 1 else ''} you are not shown; the "
                "platform runs them before anything is published. Build to the norm, not to the cases you see.\n\n"
                if held else ""
            )
            + (f"{len(documents)} documents follow." if documents else "No documents are attached.")
        ),
        "documents": documents,
    }


async def execute(sessionmaker, run_id, model: BuilderModel | None = None) -> None:
    """Carry out one run. Never raises: what went wrong is recorded on the run."""
    async with sessionmaker() as session:
        run = await session.get(BuilderRun, run_id)
        if run is None or run.state != "queued":
            return
        run.state = "running"
        await session.commit()
        try:
            await _execute(session, run, model or builder_model())
        except Exception as exc:  # noqa: BLE001 — recorded, so a person sees why
            log.exception("builder run %s failed", run_id)
            await session.rollback()
            run = await session.get(BuilderRun, run_id)
            run.state, run.error = "failed", str(exc)[:2000]
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()


async def _execute(session: AsyncSession, run: BuilderRun, model: BuilderModel) -> None:
    definition = await session.get(Definition, run.definition_id)
    draft = await repo.definition_draft(session, definition.id)
    published = await repo.latest_published_of(session, definition.id)
    base = (draft or published).content
    store = blob_store()
    documents = []
    for file_id in run.file_ids:
        stored = await repo.get_stored_file(session, _uuid(file_id), run.tenant_id)
        documents.append(Document(stored.name, stored.media_type, await store.get(stored.storage_key)))

    run.model = model.model
    turns = [_first_turn(run, base, documents)]
    content, applied, summary, rounds = base, False, "", []
    for number in range(1, MAX_ROUNDS + 1):
        try:
            proposal = await model.answer(GUIDE, turns)
        except BuilderUnavailable as exc:
            run.error = str(exc)
            break
        turns.append({"role": "assistant", "text": json.dumps(proposal, ensure_ascii=False)})
        try:
            candidate, changed = apply(content, proposal)
            check_content(candidate)
            report = await asyncio.to_thread(lambda: run_gate(workflow_from(candidate, bundles_dir()), visible(candidate)))
        except (ProposalError, DefinitionError) as exc:
            rounds.append({"round": number, "applied": False, "error": str(exc)})
            turns.append({"role": "user", "text": f"That change was not applied: {exc}\nAnswer again with a change that can be."})
            continue
        content, applied = candidate, True
        summary = str(proposal.get("summary") or summary)
        rounds.append({
            "round": number, "applied": True, "changed": changed, "passed": _passes(report),
            "failures": [f"{c['name']}: {f}" for c in report["cases"] for f in c["failures"]],
            "unverified": report.get("unverified", []),
        })
        if _passes(report):
            break
        turns.append({
            "role": "user",
            "text": (
                "The platform ran every test case. These failed:\n" + _failures(report) + "\n\n"
                "Fix the code or the rules so they follow the norm. Do not change an expected figure "
                "to fit the code; if you think one is wrong, say so in the summary and leave it."
            ),
        })
    run.rounds = rounds
    if not applied:
        run.state = "failed"
        run.error = run.error or "no answer of the builder could be applied"
        return
    before, after = _panel(base), _panel(content)
    run.guardrail_changes = [
        {"question": q, "before": before.get(q), "after": a} for q, a in after.items() if before.get(q) != a
    ]
    saved = await definitions.save_draft(session, definition.key, definition.module_key, content, definition.tenant_id)
    run.summary, run.draft_version, run.state = summary, saved.version, "succeeded"


async def interrupt_stale(sessionmaker) -> int:
    """At boot: a run left queued or running belongs to a process that no longer exists."""
    async with sessionmaker() as session:
        stale = await repo.active_builder_runs(session)
        for run in stale:
            run.state, run.error = "interrupted", "the platform restarted while it ran; start it again"
            run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return len(stale)
