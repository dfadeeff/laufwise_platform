"""Score the document reader against hand-checked inputs (ADR-0032). Calls the model: costs money.

    OPENAI_API_KEY=… python scripts/eval_extraction.py <documents dir> [--runs 3] [--model gpt-5.5]

For each test case in tests/fixtures/betriebskosten/, it reads the case's PDF, compares the
reading with the fixture field by field (free-text fields aside: a label may be worded
differently), lists what the provenance check could not find in the PDF's text, and computes the
reading. A reading is good when it differs in nothing and computes to the same balances.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from app.config import settings
from app.extraction.provenance import found_in, unconfirmed
from app.extraction.reader import Document, OpenAIReader
from app.extraction.strict import strict
from app.extraction.text import text_of
from app.kernel.modules import workflow

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "betriebskosten"
CASES = {"nk01": "NK-Test-01 Auftrag.pdf", "nk02": "NK-Test-02 Auftrag.pdf"}
FREE_TEXT = ("unit", "label", "source", "tenant")


def _same(a, b) -> bool:
    a, b = ("" if a is None else str(a)), ("" if b is None else str(b))
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return a == b


def differences(got, expected, path: str = "") -> list[str]:
    if isinstance(expected, dict):
        got = got if isinstance(got, dict) else {}
        return [
            d
            for key, value in expected.items()
            if key not in FREE_TEXT
            for d in differences(got.get(key), value, f"{path}.{key}")
        ]
    if isinstance(expected, list):
        got = got if isinstance(got, list) else []
        head = [f"{path}: {len(got)} items, expected {len(expected)}"] if len(got) != len(expected) else []
        return head + [
            d
            for i, item in enumerate(expected)
            for d in differences(got[i] if i < len(got) else None, item, f"{path}[{i}]")
        ]
    return [] if _same(got, expected) else [f"{path}: read {got!r}, expected {expected!r}"]


async def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("documents", type=Path)
    args.add_argument("--runs", type=int, default=3)
    args.add_argument("--model", default=settings.extraction_model)
    opts = args.parse_args()

    _, wf = workflow("betriebskosten")
    form = wf.schemas()["input"]
    reader = OpenAIReader(settings.openai_api_key or "", opts.model)
    perfect = total = 0
    for name, pdf in CASES.items():
        doc = Document(pdf, "application/pdf", (opts.documents / pdf).read_bytes())
        expected = json.loads(
            json.dumps(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text()), default=str)
        )
        for run in range(1, opts.runs + 1):
            started = time.monotonic()
            got = await reader.read(strict(form), wf.extraction_guide, [doc])
            diffs = differences(got, expected)
            misses = unconfirmed(got, form, found_in([text_of(doc)]))
            parsed = wf.parse(got, {"basis": "unit_share"})
            computed = wf.compute(parsed)
            balances = (
                [s["balance"] for s in computed.result["statements"]] if computed.result else None
            )
            total += 1
            perfect += not diffs
            print(
                f"{name} run {run} ({opts.model}, {time.monotonic() - started:.0f}s): "
                f"{len(diffs)} differences, {len(misses)} to check, balances {balances}"
            )
            for line in diffs + [f"to check: {m}" for m in misses]:
                print(f"    {line}")
    print(f"\n{perfect}/{total} readings identical to the hand-checked inputs")


if __name__ == "__main__":
    asyncio.run(main())
