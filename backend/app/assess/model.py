"""The model that assesses a case, with tool calls (ADR-0042 D2, D3): no FastAPI.

The model reads the case and its sources and may call the definition's tools (calculators, run in
the sandbox by the caller) as often as it needs, up to a bound. It ends with one JSON object in the
definition's assessment form. Every call and what it returned is kept: the run's record shows how
each figure the assessment names was reached. Which model is configuration (`ASSESS_MODEL`); another
provider is another class here.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from app.config import settings
from app.extraction.reader import Document, _part
from app.extraction.strict import strict

MAX_CALLS = 24  # tool calls in one assessment; past it, the assessment fails

CallTool = Callable[[str, dict], Awaitable[Any]]


class AssessUnavailable(Exception):
    """No model is configured, or it did not answer usefully."""


class AssessModel(Protocol):
    model: str

    async def assess(
        self, system: str, text: str, documents: list[Document], tools: list[dict], form: dict, call: CallTool
    ) -> tuple[dict, list[dict]]: ...


class OpenAIAssessor:
    def __init__(self, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key, self.model, self._transport = api_key, model, transport

    async def _respond(self, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=600, transport=self._transport) as client:
                res = await client.post(
                    "https://api.openai.com/v1/responses", json=body, headers={"Authorization": f"Bearer {self._key}"}
                )
        except httpx.HTTPError as exc:
            raise AssessUnavailable(f"the model did not answer: {type(exc).__name__} {exc}".strip()) from exc
        if res.status_code != 200:
            raise AssessUnavailable(f"the model refused: {res.status_code} {res.text[:300]}")
        return res.json()

    async def assess(
        self, system: str, text: str, documents: list[Document], tools: list[dict], form: dict, call: CallTool
    ) -> tuple[dict, list[dict]]:
        parts: list[dict] = [{"type": "input_text", "text": text}]
        for i, doc in enumerate(documents, 1):
            parts += [{"type": "input_text", "text": f"Dokument {i}: {doc.name}"}, _part(doc)]
        conversation: list[dict] = [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": parts},
        ]
        body = {
            "model": self.model,
            "tools": [
                {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ],
            "text": {"format": {"type": "json_schema", "name": "assessment", "schema": strict(form), "strict": True}},
        }
        trace: list[dict] = []
        while True:
            out = (await self._respond({**body, "input": conversation})).get("output", [])
            calls = [item for item in out if item.get("type") == "function_call"]
            if not calls:
                return _final(out), trace
            conversation += out  # the model's own turn, reasoning included, as it gave it
            for item in calls:
                if len(trace) >= MAX_CALLS:
                    raise AssessUnavailable(f"more than {MAX_CALLS} tool calls; the assessment does not end")
                try:
                    arguments = json.loads(item.get("arguments") or "{}")
                    result = await call(item["name"], arguments)
                    trace.append({"tool": item["name"], "arguments": arguments, "result": result})
                    output = {"result": result}
                except Exception as exc:  # noqa: BLE001 — told to the model, which may correct its call
                    arguments = item.get("arguments")
                    trace.append({"tool": item["name"], "arguments": arguments, "error": str(exc)[:300]})
                    output = {"error_message": f"{type(exc).__name__}: {str(exc)[:300]}"}
                conversation.append(
                    {"type": "function_call_output", "call_id": item["call_id"], "output": json.dumps(output, ensure_ascii=False, default=str)}
                )


def _final(out: list[dict]) -> dict:
    for item in out:
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise AssessUnavailable(f"the model declined: {part.get('refusal')}")
            if part.get("type") == "output_text":
                try:
                    return json.loads(part["text"])
                except json.JSONDecodeError as exc:
                    raise AssessUnavailable("the model answered without valid JSON") from exc
    raise AssessUnavailable("the model returned no assessment")


def assess_model() -> AssessModel:
    if not settings.openai_api_key:
        raise AssessUnavailable("assessing a case needs OPENAI_API_KEY")
    return OpenAIAssessor(settings.openai_api_key, settings.assess_model)
