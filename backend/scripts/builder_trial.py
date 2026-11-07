"""Run the builder with the real model on a known case (ADR-0039 step 6c). Calls the model: costs money.

    DATABASE_URL=postgresql+asyncpg://…@127.0.0.1:…/… OPENAI_API_KEY=… \\
    python -m scripts.builder_trial --template betriebskosten --instruction "…" \\
        --doc "NK-Test-01 Auftrag.pdf" [--doc …] [--drop-case nk01] [--hide nk01] \\
        [--replace "betriebskosten/calculate.py::old text::new text"] --out ./trial

It copies a shipped template into a throwaway workspace, prepares the draft the trial needs (a
case removed or hidden from the builder, a bug planted), runs one builder run exactly as the Studio does, and writes what
came out: the run (summary, rounds, guardrail changes) and the draft. The comparison with what is
known is printed: each case's expected figures next to the shipped case's, whether every figure
the builder wrote is unverified, and how the hidden cases come out on what it built.

Only a local database: `backend/.env` points at production, and a trial must never touch it.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.db import repo
from app.db.models import BuilderRun
from app.db.session import get_sessionmaker
from app.builder import service as builder
from app.definitions import service as definitions
from app.definitions.gate import run_gate
from app.files.service import store_file
from app.files.store import blob_store
from app.kernel.definitions import bundles_dir, workflow_from


def _local_only() -> None:
    host = urlparse(settings.database_url.replace("+asyncpg", "")).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit(f"refusing: DATABASE_URL points at {host}, not a local database")


def _prepare(content: dict, drop: list[str], hide: list[str], replace: list[str]) -> dict:
    content = copy.deepcopy(content)
    content["tests"] = [c for c in content.get("tests") or [] if c["name"] not in drop]
    for case in content["tests"]:
        if case["name"] in hide:
            case["sealed"] = True
    for spec in replace:
        path, old, new = spec.split("::")
        text = content["sources"][path]
        if text.count(old) != 1:
            raise SystemExit(f"--replace: {old!r} occurs {text.count(old)} times in {path}")
        content["sources"][path] = text.replace(old, new)
    return content


def _figures(content: dict) -> dict[str, dict]:
    return {c["name"]: (c.get("expect") or {}).get("result") or {} for c in content.get("tests") or []}


async def trial(args) -> None:
    _local_only()
    maker = get_sessionmaker()
    async with maker() as session:
        await definitions.seed_from_files(session)
        tenant = await repo.tenant_for_org(session, f"org_builder_trial_{uuid.uuid4().hex[:8]}", "Builder trial")
        await session.commit()
        draft = await definitions.create_from_template(session, tenant.id, args.template, f"{args.template} (trial)")
        key, shipped = draft.content["key"], draft.content
        prepared = _prepare(shipped, args.drop_case, args.hide, args.replace)
        await definitions.save_draft(session, key, draft.content["module"], prepared, tenant.id)
        file_ids = []
        for doc in args.doc:
            stored, _ = await store_file(session, blob_store(), tenant.id, Path(doc).name, Path(doc).read_bytes())
            file_ids.append(str(stored.id))
        definition = await repo.get_definition(session, key)
        run = await builder.start(session, definition, args.instruction, file_ids, "builder-trial")
        run_id = run.id

    await builder.execute(maker, run_id)

    async with maker() as session:
        run = await session.get(BuilderRun, run_id)
        definition = await repo.get_definition(session, key)
        final = (await repo.definition_draft(session, definition.id)).content
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    record = {k: getattr(run, k) for k in ("state", "model", "rounds", "summary", "guardrail_changes", "draft_version", "error")}
    (out / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))
    (out / "draft.json").write_text(json.dumps(final, ensure_ascii=False, indent=2))

    print(f"state: {run.state} · model: {run.model} · rounds: {len(run.rounds)}")
    for r in run.rounds:
        print(f"  round {r['round']}: applied={r['applied']} passed={r.get('passed')} {r.get('error') or ''}")
        for f in r.get("failures") or []:
            print(f"    - {f}")
        if r.get("changed"):
            print(f"    changed: {', '.join(r['changed'])}")
    if run.error:
        print(f"error: {run.error}")
    print("\nsummary:\n" + (run.summary or "(none)"))

    known, written = _figures(shipped), _figures(final)
    print("\nexpected figures, the builder's against the shipped case's:")
    for name, figures in written.items():
        for path, figure in figures.items():
            ship = known.get(name, {}).get(path)
            mark = "same" if ship and str(ship["value"]) == str(figure["value"]) else ("DIFFERS" if ship else "new")
            print(f"  {name} {path} = {figure['value']} [{mark}] unverified={bool(figure.get('unverified'))} ref={figure.get('ref')!r}")
    for path in set(shipped["sources"]) | set(final["sources"]):
        if shipped["sources"].get(path) != final["sources"].get(path):
            print(f"  source differs from the shipped one: {path}")

    if args.hide:
        report = run_gate(workflow_from(final, bundles_dir()), final)
        print("\nhidden cases, on what the builder built:")
        for case in (c for c in report["cases"] if c["sealed"]):
            print(f"  {case['name']}: {'passed' if case['passed'] else 'FAILED'}")
            for failure in case["failures"]:
                print(f"    - {failure}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--template", required=True)
    p.add_argument("--instruction", required=True)
    p.add_argument("--doc", action="append", default=[])
    p.add_argument("--drop-case", action="append", default=[])
    p.add_argument("--hide", action="append", default=[])
    p.add_argument("--replace", action="append", default=[])
    p.add_argument("--out", required=True)
    asyncio.run(trial(p.parse_args()))


if __name__ == "__main__":
    main()
